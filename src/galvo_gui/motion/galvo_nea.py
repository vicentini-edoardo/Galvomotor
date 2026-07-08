"""Real galvo (galvo_functions/GB511) and neaSNOM (nea_tools) backends.

These are two independent connections:

* :class:`RealGalvoBackend` opens the galvomotor board and drives XY motion.
* :class:`RealNeaBackend` opens the neaSNOM session and drives the parabolic
  mirror Z axis plus optical signal readout.

They share nothing at runtime beyond the process-global SDK session dictionaries
(``_GB511_SHARED`` and ``_NEA_SESSION``), which pin their state to the process
on first use and must be reused on reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import inspect
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Tuple

import numpy as np

from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_PULSES,
    Z_STEP_OPTIONS_NM,
    GalvoBackend,
    GalvoError,
    NeaBackend,
    SnomSample,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _REPO_ROOT / "config_files"
_DEFAULT_CAL_FILES_PATH = _CONFIG_DIR / "cal_files"
_DEFAULT_CORRECTION_FILE = "GM-2020-ftheta-10mm-fo4.tsc"

if str(_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_DIR))

try:
    from galvo_functions import Galvo as _GalvoHW  # noqa: F401
    from galvo_functions import open_galvo as _open_galvo  # noqa: F401
    GALVO_AVAILABLE = True
except ImportError:
    GALVO_AVAILABLE = False

try:
    import nea_tools
    import nest_asyncio
    NEA_AVAILABLE = True
except ImportError:
    NEA_AVAILABLE = False

_N_HARMONICS = 6
# Give the galvo time to act on a move before the read-back that detects
# silently dropped moves (galvo settling is sub-ms; this covers DLL latency).
_MOVE_SETTLE_S = 0.05
_MOVE_FOLLOW_TIMEOUT_S = 0.25
_MOVE_FOLLOW_POLL_S = 0.02
_OFFSET_CALIBRATION_REPEATS = 3
_OFFSET_CALIBRATION_NOISE_P = 20.0
_OFFSET_CALIBRATION_STEP_P = 500.0
_DEFAULT_AXIS_FOLLOW_TOLERANCE_PULSES = 5
_STREAM_POLL_S = 0.02

# The GB511 board uses two coordinate spaces: ctr_get_current_xy_pos reports
# fine "read" pulses, while ctr_goto_xy expects coarser command units.  The
# conversion (goto units per read pulse) comes from galvo_move() in the
# working lab notebook (260220 - Galvo-Parabolic-snom-scan.ipynb, CX/CY).
# galvo_functions.Galvo.Move/GoHome omit this conversion and command targets
# ~9x out of range, railing one axis against its limit — do not use them.
_GOTO_PER_READ_X = 0.11080
_GOTO_PER_READ_Y = 0.11080
_X_GOTO_BIAS = 12

# -2**19: value the GB511 reports on both axes when the position read-back is
# not live (board not selected, held by another client, or the GC-211/212
# drivers are not following the high-speed link) — see galvo_debug.py.
_READBACK_SENTINEL = -(2**19)

# Process-wide SDK state shared by every backend instance.  Both halves of
# the stack pin themselves to the process on first use, so reconnects must
# reuse them instead of recreating them:
#   - The GB511 board is single-client and galvo_functions has no close API;
#     re-running open_galvo() on the live board (reloading the DSP program
#     and re-selecting the board) kills the read-back — both axes then report
#     _READBACK_SENTINEL and every move is silently ignored.
#   - nea_tools does not survive a disconnect→connect cycle inside one
#     process: after calling nea_tools.disconnect(), the next session hangs
#     in the mirror path (Z reference read or the first Z move) no matter
#     how the asyncio loop is managed.  The session opened first therefore
#     stays live for the whole process — like the lab notebooks, which
#     connect once per kernel — and every later connect adopts it.
_GB511_SHARED: dict[str, Any] = {"wrap": None, "kwargs": None}
_NEA_SESSION: dict[str, Any] = {
    "loop": None,
    "host": None,
    "connected": False,
    "context": None,
    "stream": None,
    "mirror_cls": None,
}


class _StatusReporterMixin:
    """Shared status-callback plumbing for the real backends."""

    _status_callback: Callable[[str], None] | None = None
    _backend_label = "Backend"

    def set_status_callback(self, callback: Callable[[str], None] | None) -> None:
        self._status_callback = callback

    def _report_status(self, message: str) -> None:
        callback = getattr(self, "_status_callback", None)
        if callback is None:
            return
        backend_label = getattr(self, "_backend_label", type(self).__name__)
        with contextlib.suppress(Exception):
            callback(f"{backend_label}: {message}")


class RealGalvoBackend(_StatusReporterMixin, GalvoBackend):
    """Real galvomotor XY backend using galvo_functions + the GB511 board.

    Requires ``galvo_functions`` on sys.path (lab PC).  Import-guarded:
    instantiation raises GalvoError if the module is missing.
    """

    def __init__(
        self,
        cal_files_path: str = str(_DEFAULT_CAL_FILES_PATH),
        *,
        axis_follow_tolerance_pulses: int = _DEFAULT_AXIS_FOLLOW_TOLERANCE_PULSES,
    ) -> None:
        if not GALVO_AVAILABLE:
            raise GalvoError(
                "galvo_functions not available. "
                "Ensure galvo_functions.py is on sys.path."
            )
        self._cal_path = str(_resolve_repo_path(cal_files_path))
        self._connected = False
        # Duck-typed SDK/DLL handles (None until connect); Any keeps mypy out
        # of vendor attribute lookups like Galvo.Move.
        self._galvo: Any = None  # galvo_functions.Galvo instance
        self._gb511_wrap: Any = None  # CanonGB511 low-level wrapper
        # Active home offset, in read pulses relative to the calibrated centre.
        self._home_x_p: float = 0.0
        self._home_y_p: float = 0.0
        # Last goto units commanded per axis. ctr_goto_xy always commands both
        # axes, so the axis a jog does not move is re-commanded to this stored
        # value instead of a value re-quantised from the noisy read-back —
        # otherwise encoder noise near a goto-unit boundary nudges the parked
        # axis on every jog (X and Y appear coupled). None until first command.
        self._cmd_gx: int | None = None
        self._cmd_gy: int | None = None
        self._x_goto_bias = _X_GOTO_BIAS
        self._axis_follow_tolerance_pulses = max(1, int(axis_follow_tolerance_pulses))
        self._offset_x_p = 0.0
        self._offset_y_p = 0.0
        self._offset_enabled = True
        self._has_last_good_offset = False
        self._status_callback: Callable[[str], None] | None = None
        self._backend_label = "Galvo"
        self._last_move_diag: dict[str, int | float] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "") -> None:
        """Initialise the galvomotor board and validate its read-back."""
        self._report_status("Starting galvo connection.")
        self._report_status("Opening galvo hardware...")
        self._open_galvo_hardware()
        self._report_status("Galvo hardware ready.")
        self._report_status("Validating hardware read-back...")
        self._validate_readback()
        self._connected = True
        self._auto_calibrate_offset()
        self._report_status("Galvo connection complete.")

    def disconnect(self) -> None:
        self._report_status("Starting galvo disconnect.")
        # ponytail: keep galvo_functions open — no close_galvo() in notebooks
        self._connected = False
        self._gb511_wrap = None
        self._report_status("Galvo disconnect complete.")

    def is_connected(self) -> bool:
        return self._connected

    def set_offset_correction_enabled(self, enabled: bool) -> None:
        self._offset_enabled = bool(enabled)

    def offset_correction_enabled(self) -> bool:
        return self._offset_enabled

    def run_offset_calibration(self) -> Tuple[float, float]:
        self._require_connected()
        self._report_status("Running XY offset calibration...")
        start_x, start_y = self._read_gb511_bits()
        saved_offsets = (self._offset_x_p, self._offset_y_p)
        saved_enabled = self._offset_enabled
        saved_has_last_good = self._has_last_good_offset
        try:
            self._offset_enabled = False
            self._offset_x_p = 0.0
            self._offset_y_p = 0.0
            x_samples = self._collect_axis_offset_samples("x", _OFFSET_CALIBRATION_STEP_P)
            y_samples = self._collect_axis_offset_samples("y", _OFFSET_CALIBRATION_STEP_P)
            x_usable = _filtered_offset_samples(x_samples, _OFFSET_CALIBRATION_NOISE_P)
            y_usable = _filtered_offset_samples(y_samples, _OFFSET_CALIBRATION_NOISE_P)
            if x_usable:
                self._offset_x_p = _average_axis_offset(x_samples, _OFFSET_CALIBRATION_NOISE_P)
            else:
                self._offset_x_p = saved_offsets[0] if saved_has_last_good else 0.0
            if y_usable:
                self._offset_y_p = _average_axis_offset(y_samples, _OFFSET_CALIBRATION_NOISE_P)
            else:
                self._offset_y_p = saved_offsets[1] if saved_has_last_good else 0.0
            self._has_last_good_offset = bool(x_usable or y_usable or saved_has_last_good)
            if not x_usable and not y_usable and not saved_has_last_good:
                self._report_status(
                    "Offset calibration warning: all samples were within the ±20 pulse noise band."
                )
            else:
                self._report_status(
                    f"Offset calibration result: X={self._offset_x_p:.0f} pulses, "
                    f"Y={self._offset_y_p:.0f} pulses."
                )
            self._goto_read_units(
                start_x,
                start_y,
                ref=self._read_gb511_bits(),
                x_offset_p=self._offset_x_p,
                y_offset_p=self._offset_y_p,
            )
            time.sleep(_MOVE_SETTLE_S)
            return (self._offset_x_p, self._offset_y_p)
        except Exception:
            self._offset_x_p, self._offset_y_p = saved_offsets
            self._offset_enabled = saved_enabled
            self._has_last_good_offset = saved_has_last_good
            raise
        finally:
            self._offset_enabled = saved_enabled

    def _auto_calibrate_offset(self) -> None:
        try:
            self.run_offset_calibration()
        except Exception as exc:  # noqa: BLE001
            self._offset_x_p = 0.0
            self._offset_y_p = 0.0
            self._has_last_good_offset = False
            self._report_status(f"Offset calibration warning: {exc}")

    def _open_galvo_hardware(self) -> None:
        open_kwargs: dict[str, Any] = {"CalFn": _DEFAULT_CORRECTION_FILE}
        try:
            signature = inspect.signature(_open_galvo)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "idx_board" in signature.parameters:
            board_index = getattr(self, "_board_index", None)
            if board_index is not None:
                open_kwargs["idx_board"] = int(board_index)
        program_file = getattr(self, "_program_file", None)
        if program_file is not None:
            if signature is None:
                raise GalvoError(
                    "Custom GB511 program file requested, but open_galvo does not expose "
                    "a usable signature for wiring that override."
                )
            for parameter_name in ("program_file", "ProgramFn", "dsp_file", "DSPFile"):
                if parameter_name in signature.parameters:
                    open_kwargs[parameter_name] = program_file
                    break
            else:
                raise GalvoError(
                    "Custom GB511 program file requested, but this galvo_functions.open_galvo "
                    "implementation does not support overriding it."
                )
        if _GB511_SHARED["wrap"] is not None and _GB511_SHARED["kwargs"] == open_kwargs:
            # Board already open in this process: reuse the handle instead of
            # re-running open_galvo(), which would reload the DSP program into
            # the live board and leave the read-back dead (sentinel on both
            # axes). This mirrors the notebooks, which open the board once
            # and never close it.
            self._gb511_wrap = _GB511_SHARED["wrap"]
            self._report_status("Reusing galvo board opened earlier in this session.")
        else:
            with _working_directory(_CONFIG_DIR):
                self._gb511_wrap, _status = _open_galvo(**open_kwargs)
            _GB511_SHARED["wrap"] = self._gb511_wrap
            _GB511_SHARED["kwargs"] = dict(open_kwargs)
            self._report_status("Galvo board handle opened.")
        self._galvo = _GalvoHW(self._cal_path)

    def _validate_readback(self) -> None:
        # Fail loudly now if the board rejects position reads, instead of
        # connecting into a dead board that silently ignores every move.
        x_bits, y_bits = self._read_gb511_bits()
        if x_bits == _READBACK_SENTINEL and y_bits == _READBACK_SENTINEL:
            raise GalvoError(
                f"GB511 position read-back is not live: both axes report the "
                f"{_READBACK_SENTINEL} sentinel. The drivers are not following "
                "the board (servo off, not in high-speed mode, axes not homed, "
                "or the board is held by another client such as the Canon "
                "control software). Fix the driver state and reconnect."
            )
        self._report_status("GB511 position read-back OK.")

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_relative_pulses(self, dx_p: float, dy_p: float) -> None:
        """Move galvo by (dx_p, dy_p) encoder pulses relative to current position.

        Replicates the working notebook's scan() pattern: read the current
        position in read pulses, add the pulse displacement, and command the
        absolute target through the galvo_move() goto-unit conversion.
        galvo_functions.Galvo.Move is not used: it is absolute-from-home
        (repeated jogs would not accumulate) and it skips the goto-unit
        conversion (targets land ~9x out of range).

        ctr_goto_xy always commands both axes, so an axis with a zero request
        is held at its last commanded goto unit (``_cmd_g*``).  Re-deriving it
        from the read-back instead lets encoder noise flip it by a goto unit
        (~9 read pulses) near a rounding boundary, which is why jogging one
        axis appeared to move the other.
        """
        self._require_connected()
        xb_before, yb_before = self._read_gb511_bits()
        gx_cur = round(_GOTO_PER_READ_X * xb_before)
        gy_cur = round(_GOTO_PER_READ_Y * yb_before)

        if dx_p:
            gx_target = self._read_target_to_goto("x", xb_before + dx_p)
        else:
            gx_target = self._parked_goto(getattr(self, "_cmd_gx", None), gx_cur)
        if dy_p:
            gy_target = self._read_target_to_goto("y", yb_before + dy_p)
        else:
            gy_target = self._parked_goto(getattr(self, "_cmd_gy", None), gy_cur)
        cmd_gx_before = getattr(self, "_cmd_gx", None)
        cmd_gy_before = getattr(self, "_cmd_gy", None)
        self._last_move_diag = {
            "requested_step_x": dx_p,
            "requested_step_y": dy_p,
            "before_x_read": xb_before,
            "before_y_read": yb_before,
            "target_x_read": xb_before + dx_p,
            "target_y_read": yb_before + dy_p,
            "target_x_goto": gx_target,
            "target_y_goto": gy_target,
            "last_cmd_gx_before": gx_cur if cmd_gx_before is None else cmd_gx_before,
            "last_cmd_gy_before": gy_cur if cmd_gy_before is None else cmd_gy_before,
            "cmd_gx_sent": gx_target,
            "cmd_gy_sent": gy_target,
        }

        if (gx_target, gy_target) == (gx_cur, gy_cur):
            # The requested move quantises onto the current position: no motion
            # can be expected, but remember the (unchanged) command anyway.
            self._cmd_gx, self._cmd_gy = gx_target, gy_target
            self._last_move_diag.update(
                {
                    "after_x_read": xb_before,
                    "after_y_read": yb_before,
                    "after_x_goto_equiv": gx_cur,
                    "after_y_goto_equiv": gy_cur,
                    "x_error_pulses": xb_before - (xb_before + dx_p),
                    "y_error_pulses": yb_before - (yb_before + dy_p),
                }
            )
            return

        self._command_goto(gx_target, gy_target)
        time.sleep(_MOVE_SETTLE_S)
        xb_after, yb_after = self._wait_for_axis_follow(
            dx_p,
            dy_p,
            xb_before + dx_p,
            yb_before + dy_p,
        )
        if (xb_after, yb_after) == (xb_before, yb_before):
            self._record_move_follow_diag(
                xb_after,
                yb_after,
                x_target_p=xb_before + dx_p,
                y_target_p=yb_before + dy_p,
            )
            raise GalvoError(
                f"Galvo move ({dx_p:g}, {dy_p:g}) pulses produced no motion: read-back "
                f"stayed at ({xb_before}, {yb_before}) pulses. The axis may be at its "
                "range limit — recenter with GoHome (⊙) and retry."
            )
        self._record_move_follow_diag(
            xb_after,
            yb_after,
            x_target_p=xb_before + dx_p,
            y_target_p=yb_before + dy_p,
        )
        self._validate_axis_follow(
            dx_p,
            dy_p,
            xb_before + dx_p,
            yb_before + dy_p,
            xb_after,
            yb_after,
        )

    def _wait_for_axis_follow(
        self,
        dx_p: float,
        dy_p: float,
        x_target_p: float,
        y_target_p: float,
    ) -> Tuple[int, int]:
        deadline = time.monotonic() + _MOVE_FOLLOW_TIMEOUT_S
        last = self._read_gb511_bits()
        while True:
            try:
                self._validate_axis_follow(dx_p, dy_p, x_target_p, y_target_p, *last)
            except GalvoError:
                if time.monotonic() >= deadline:
                    return last
                time.sleep(_MOVE_FOLLOW_POLL_S)
                last = self._read_gb511_bits()
                continue
            return last

    @staticmethod
    def _parked_goto(commanded: int | None, fallback: int) -> int:
        """Goto units for an axis that is not moving: the last commanded value,
        or the current read-back quantisation before any command was issued."""
        return int(commanded) if commanded is not None else int(fallback)

    def read_xy_pulses(self) -> Tuple[float, float]:
        """Return galvo position from Read() as (x, y) read pulses from centre."""
        self._require_connected()
        x_p, y_p = self._read_xy_pulses_relative_to_startup_home()
        home_x_p, home_y_p = self._current_home_xy_pulses()
        return (x_p - home_x_p, y_p - home_y_p)

    def last_move_diagnostics(self) -> dict[str, int | float]:
        return dict(self._last_move_diag)

    def _collect_axis_offset_samples(self, axis: str, step_p: float) -> list[float]:
        samples: list[float] = []
        for _ in range(_OFFSET_CALIBRATION_REPEATS):
            for sign in (1.0, -1.0):
                start_x, start_y = self._read_gb511_bits()
                dx_p = sign * step_p if axis == "x" else 0.0
                dy_p = sign * step_p if axis == "y" else 0.0
                try:
                    self.move_relative_pulses(dx_p, dy_p)
                except GalvoError:
                    pass
                diag = self.last_move_diagnostics()
                before_key = "before_x_read" if axis == "x" else "before_y_read"
                after_key = "after_x_read" if axis == "x" else "after_y_read"
                before = float(diag[before_key])
                after = float(diag[after_key])
                commanded = dx_p if axis == "x" else dy_p
                sample = (after - before) - commanded
                samples.append(sample)
                self._goto_read_units(
                    start_x,
                    start_y,
                    ref=self._read_gb511_bits(),
                    x_offset_p=sample if axis == "x" else 0.0,
                    y_offset_p=sample if axis == "y" else 0.0,
                )
                time.sleep(_MOVE_SETTLE_S)
        return samples

    def set_home_pulses(
        self, x_p: float | None = None, y_p: float | None = None
    ) -> Tuple[float, float]:
        self._require_connected()
        if (x_p is None) != (y_p is None):
            raise ValueError("x_p and y_p must be provided together.")
        if x_p is None or y_p is None:
            self._home_x_p, self._home_y_p = self._read_xy_pulses_relative_to_startup_home()
        else:
            self._home_x_p = float(x_p)
            self._home_y_p = float(y_p)
        return (self._home_x_p, self._home_y_p)

    def goto_center(self) -> None:
        """Move galvo back to the active home position."""
        self._require_connected()
        # Not Galvo.GoHome: it feeds read-unit pulses straight into
        # ctr_goto_xy without the goto-unit conversion (see _GOTO_PER_READ_X).
        # The calibrated centre sits at K*X0 read pulses; add the active home.
        home_x_p, home_y_p = self._current_home_xy_pulses()
        self._goto_read_units(
            self._galvo.K * self._galvo.X0 + home_x_p,
            self._galvo.K * self._galvo.Y0 + home_y_p,
        )

    def available_xy_steps_pulses(self) -> tuple[float, ...]:
        self._require_connected()
        # A step is usable only if it changes the coarser goto-unit target.
        return _available_xy_steps_pulses(_GOTO_PER_READ_X)

    def pulses_per_nm(self) -> float:
        return float(self._galvo.K)

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("Galvo backend is not connected.")

    def _read_gb511_bits(self) -> Tuple[int, int]:
        """Read current galvo position in bits, with output params passed correctly.

        ``ctypes`` passes a bare ``c_long`` **by value**: a raw DLL handle
        would receive the integer 0 instead of a pointer and could never
        write the position back, so every read would report 0 bits forever
        (which is exactly what breaks relative moves).  Raw DLL handles get
        ``byref``; wrapper objects keep receiving the ``c_long`` instances.
        """
        x_bit = ctypes.c_long()
        y_bit = ctypes.c_long()
        if _is_raw_dll(self._gb511_wrap):
            args: Tuple[Any, ...] = (ctypes.byref(x_bit), ctypes.byref(y_bit))
        else:
            args = (x_bit, y_bit)
        self._call_gb511("ctr_get_current_xy_pos", *args)
        return (int(x_bit.value), int(y_bit.value))

    def _read_xy_pulses_relative_to_startup_home(self) -> Tuple[float, float]:
        # Read pulses relative to the calibrated centre, which sits at K*X0
        # read pulses (the same linear scale ctr_goto_xy is commanded through).
        xb, yb = self._read_gb511_bits()
        x_p = xb - self._galvo.K * self._galvo.X0
        y_p = yb - self._galvo.K * self._galvo.Y0
        return (float(x_p), float(y_p))

    def _validate_axis_follow(
        self,
        dx_p: float,
        dy_p: float,
        x_target_p: float,
        y_target_p: float,
        xb_after: int,
        yb_after: int,
    ) -> None:
        x_tol_p = max(
            1,
            int(
                getattr(
                    self,
                    "_axis_follow_tolerance_pulses",
                    _DEFAULT_AXIS_FOLLOW_TOLERANCE_PULSES,
                )
            ),
        )
        y_tol_p = x_tol_p
        if dx_p and abs(xb_after - x_target_p) > x_tol_p:
            raise GalvoError(
                f"X axis read-back missed the commanded pulse target: requested "
                f"{x_target_p:.0f} pulses, read back {xb_after}. The axis may be at its "
                "movement boundary — recenter with GoHome (⊙) and retry."
            )
        if dy_p and abs(yb_after - y_target_p) > y_tol_p:
            raise GalvoError(
                f"Y axis read-back missed the commanded pulse target: requested "
                f"{y_target_p:.0f} pulses, read back {yb_after}. The axis may be at its "
                "movement boundary — recenter with GoHome (⊙) and retry."
            )

    def _current_home_xy_pulses(self) -> Tuple[float, float]:
        return (float(getattr(self, "_home_x_p", 0.0)), float(getattr(self, "_home_y_p", 0.0)))

    def _record_move_follow_diag(
        self,
        xb_after: int,
        yb_after: int,
        *,
        x_target_p: float,
        y_target_p: float,
    ) -> None:
        self._last_move_diag.update(
            {
                "after_x_read": xb_after,
                "after_y_read": yb_after,
                "after_x_goto_equiv": round(_GOTO_PER_READ_X * xb_after),
                "after_y_goto_equiv": round(_GOTO_PER_READ_Y * yb_after),
                "x_error_pulses": xb_after - x_target_p,
                "y_error_pulses": yb_after - y_target_p,
            }
        )

    def _goto_read_units(
        self,
        x_read: float,
        y_read: float,
        ref: Tuple[int, int] | None = None,
        *,
        x_offset_p: float | None = None,
        y_offset_p: float | None = None,
    ) -> bool:
        """Command an absolute target given in read pulses (notebook galvo_move).

        The read→goto conversion must use round(), never int(): ctr_goto_xy
        commands BOTH axes, so an axis that is not meant to move gets its
        target re-derived from the quantised read-back on every call.  With
        truncation that re-derivation lands one goto unit low about half the
        time (int(CX * round(g / CX)) == g - 1), so jogging X dragged Y along
        (and vice versa), and every back-and-forth pair on the moving axis
        drifted one goto unit (~9 read pulses) per round trip.

        Returns False when the target quantises onto the goto-unit position of
        *ref* (the current position, read if not supplied), i.e. the request is
        below the board's command resolution and no motion can be expected.
        """
        gx = self._read_target_to_goto("x", x_read, x_offset_p=x_offset_p)
        gy = self._read_target_to_goto("y", y_read, y_offset_p=y_offset_p)
        xb_now, yb_now = ref if ref is not None else self._read_gb511_bits()
        self._command_goto(gx, gy)
        return (gx, gy) != (
            self._read_target_to_goto("x", xb_now, x_offset_p=x_offset_p),
            self._read_target_to_goto("y", yb_now, y_offset_p=y_offset_p),
        )

    def _read_target_to_goto(
        self,
        axis: str,
        read_target_p: float,
        *,
        x_offset_p: float | None = None,
        y_offset_p: float | None = None,
    ) -> int:
        if axis == "x":
            offset = getattr(self, "_offset_x_p", 0.0) if x_offset_p is None else float(x_offset_p)
            enabled = getattr(self, "_offset_enabled", True)
            corrected = read_target_p - (offset if enabled or x_offset_p is not None else 0.0)
            return round(_GOTO_PER_READ_X * corrected) + int(getattr(self, "_x_goto_bias", 0))
        offset = getattr(self, "_offset_y_p", 0.0) if y_offset_p is None else float(y_offset_p)
        enabled = getattr(self, "_offset_enabled", True)
        corrected = read_target_p - (offset if enabled or y_offset_p is not None else 0.0)
        return round(_GOTO_PER_READ_Y * corrected)

    def _command_goto(self, gx: int, gy: int) -> None:
        """Command absolute goto units on both axes and remember them so a
        later single-axis jog can hold the parked axis exactly here."""
        self._call_gb511("ctr_goto_xy", int(gx), int(gy))
        self._cmd_gx = int(gx)
        self._cmd_gy = int(gy)

    def _call_gb511(self, name: str, *args: Any) -> Any:
        """Call a GB511 function, translating failures into GalvoError.

        The board signals refusal through nonzero return codes; ignoring
        them (as earlier revisions did) turns a dead board into a silent
        no-op.  ctypes-level faults (e.g. access violations) are wrapped so
        the GUI can report them instead of killing the pythonw process.
        """
        if self._gb511_wrap is None:
            raise GalvoError(f"GB511 board is not open for {name}.")
        func = getattr(self._gb511_wrap, name, None)
        if func is None:
            raise GalvoError(f"GB511 wrapper has no function {name}.")
        try:
            result = func(*args)
        except GalvoError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GalvoError(f"{name} failed: {exc}") from exc
        if isinstance(result, int) and result != 0:
            raise GalvoError(f"{name} failed with code {result}.")
        return result


class RealNeaBackend(_StatusReporterMixin, NeaBackend):
    """Real neaSNOM backend: parabolic-mirror Z axis + optical signal readout.

    Requires ``nea_tools`` + ``nest_asyncio`` installed and a neaSNOM server
    reachable at *host*.  Import-guarded: instantiation raises GalvoError if
    the libraries are missing.
    """

    def __init__(self) -> None:
        if not NEA_AVAILABLE:
            raise GalvoError(
                "nea_tools not available. Run: pip install nea_tools nest_asyncio"
            )
        self._connected = False
        self._loop: Any = None  # asyncio event loop
        self._context: Any = None  # neaspec.context (post-connect)
        self._stream_module: Any = None  # nea_tools.microscope.stream
        self._mirror_cls: Any = None
        self._z0_nm: float = 0.0
        self._z_nm: float = 0.0
        self._status_callback: Callable[[str], None] | None = None
        self._backend_label = "neaSNOM"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "nea-server") -> None:
        """Connect to the neaSNOM server and capture the mirror Z reference."""
        self._report_status(f"Starting connection to {host}.")
        self._report_status("Opening neaSNOM session...")
        self._connect_nea_session(host)
        self._report_status("neaSNOM session ready.")
        self._report_status("Capturing mirror Z reference...")
        self._z0_nm = self._read_absolute_mirror_z_nm()
        self._z_nm = 0.0
        self._connected = True
        self._report_status("Connection complete.")

    def disconnect(self) -> None:
        should_disconnect_session = self._connected or self._loop is not None
        self._report_status("Starting disconnect.")
        self._connected = False
        self._mirror_cls = None
        self._stream_module = None
        self._context = None
        if should_disconnect_session:
            self._disconnect_nea_session()
        self._report_status("Disconnect complete.")

    def is_connected(self) -> bool:
        return self._connected

    def _connect_nea_session(self, host: str) -> None:
        session = _NEA_SESSION
        if session["connected"] and session["host"] == host:
            # nea_tools cannot re-connect inside one process (see the note at
            # the top of the module): adopt the live session instead.
            self._adopt_nea_session()
            self._report_status("Reusing live neaSNOM session.")
            return
        if session["connected"]:
            self._report_status(
                "Host changed; closing the previous neaSNOM session. If the "
                "new connection hangs, restart the application."
            )
            self._shutdown_nea_session()
        loop = session["loop"]
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            nest_asyncio.apply(loop)
            session["loop"] = loop
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            nea_tools.connect(host, fingerprint=None, path_to_dll="")
        )
        # These imports only work after nea_tools.connect has loaded the SDK.
        import neaspec  # noqa: PLC0415
        from nea_tools.microscope import stream  # noqa: PLC0415
        from nea_tools.microscope.motors import Mirror  # noqa: PLC0415
        session["host"] = host
        session["connected"] = True
        session["context"] = neaspec.context
        session["stream"] = stream
        session["mirror_cls"] = Mirror
        self._adopt_nea_session()
        self._report_status("neaSNOM SDK objects loaded.")

    def _adopt_nea_session(self) -> None:
        self._loop = _NEA_SESSION["loop"]
        with contextlib.suppress(Exception):
            asyncio.set_event_loop(self._loop)
        self._context = _NEA_SESSION["context"]
        self._stream_module = _NEA_SESSION["stream"]
        self._mirror_cls = _NEA_SESSION["mirror_cls"]

    def _disconnect_nea_session(self) -> None:
        # Deliberately NOT nea_tools.disconnect(): the SDK cannot re-connect
        # inside one process (reconnects then hang in the mirror path), so
        # the session stays live for the next connection and dies with the
        # process — like the lab notebooks, which connect once per kernel.
        self._loop = None
        self._report_status("Keeping neaSNOM session open for the next connection.")

    def _shutdown_nea_session(self) -> None:
        """Really close the nea session (host change only — see module note)."""
        session = _NEA_SESSION
        loop = session["loop"]
        try:
            if loop is not None and not loop.is_closed():
                with contextlib.suppress(Exception):
                    asyncio.set_event_loop(loop)
            with contextlib.suppress(Exception):
                disconnect_result = nea_tools.disconnect()
                if inspect.isawaitable(disconnect_result):
                    if loop is not None and not loop.is_closed():
                        loop.run_until_complete(disconnect_result)
                    else:

                        async def _await_disconnect(awaitable: Any) -> None:
                            await awaitable

                        asyncio.run(_await_disconnect(disconnect_result))
        finally:
            session["host"] = None
            session["connected"] = False
            session["context"] = None
            session["stream"] = None
            session["mirror_cls"] = None
            self._report_status("Previous neaSNOM session closed.")

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_z_relative(self, dz_nm: float) -> None:
        self._require_connected()
        with self._open_mirror() as mirror:
            mirror.go_relative(0, 0, dz_nm)
            self._wait_for_mirror(mirror)
            # Read back the hardware position so the GUI reports what the
            # mirror actually did, not what we asked for.
            z_abs_nm = float(mirror.absolute_position[2])
        self._z_nm = z_abs_nm - self._z0_nm

    def read_z_nm(self) -> float:
        self._require_connected()
        return self._z_nm

    def available_z_steps_nm(self) -> tuple[float, ...]:
        self._require_connected()
        return Z_STEP_OPTIONS_NM

    # ------------------------------------------------------------------
    # Signal readout
    # ------------------------------------------------------------------

    def read_sample(
        self,
        t_integ_s: float = 0.05,
        xy_nm: Tuple[float, float] = (0.0, 0.0),
        xy_pulses: Tuple[float, float] = (0.0, 0.0),
    ) -> SnomSample:
        """Read optical amplitude and phase via neaSNOM stream (averaged over t_integ_s)."""
        self._require_connected()
        amp_keys = [f"O{h}A" for h in range(_N_HARMONICS)]
        phase_keys = [f"O{h}P" for h in range(_N_HARMONICS)]
        keys = amp_keys + phase_keys
        amp_totals = {k: 0.0 for k in amp_keys}
        phase_sin = {k: 0.0 for k in phase_keys}
        phase_cos = {k: 0.0 for k in phase_keys}
        counts = {k: 0 for k in keys}

        with self._stream_module.Stream() as s:
            t_end = time.monotonic() + t_integ_s
            while time.monotonic() < t_end:
                for k in keys:
                    try:
                        v = float(s.data[k][-1])
                        if not math.isfinite(v):
                            continue
                        if k in amp_totals:
                            amp_totals[k] += v
                        else:
                            phase_sin[k] += math.sin(v)
                            phase_cos[k] += math.cos(v)
                        counts[k] += 1
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(_STREAM_POLL_S)

        if not any(counts.values()):
            self._report_status("Warning: no neaSNOM stream data received; recording NaN for this pixel.")

        def _get_amp(k: str) -> float:
            return amp_totals[k] / counts[k] if counts[k] else float("nan")

        def _get_phase(k: str) -> float:
            return math.atan2(phase_sin[k], phase_cos[k]) if counts[k] else float("nan")

        return SnomSample(
            xy_nm=(float(xy_nm[0]), float(xy_nm[1])),
            o_amp=np.array([_get_amp(f"O{h}A") for h in range(_N_HARMONICS)]),
            o_phase=np.array([_get_phase(f"O{h}P") for h in range(_N_HARMONICS)]),
            xy_pulses=(float(xy_pulses[0]), float(xy_pulses[1])),
        )

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("neaSNOM backend is not connected.")

    def _read_absolute_mirror_z_nm(self) -> float:
        with self._open_mirror() as mirror:
            return float(mirror.absolute_position[2])

    def _open_mirror(self):  # type: ignore[no-untyped-def]
        self._require_connected_or_ready()
        return self._mirror_cls()

    def _require_connected_or_ready(self) -> None:
        if self._mirror_cls is None:
            raise GalvoError("Mirror controls are not available.")

    def _wait_for_mirror(self, mirror: Any) -> None:
        waiter = getattr(mirror, "await_async", None)
        if waiter is not None:
            self._loop.run_until_complete(waiter())


def _is_raw_dll(obj: Any) -> bool:
    """True when *obj* is a bare ctypes library handle (needs byref for out-params)."""
    return isinstance(obj, ctypes.CDLL)


def _resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else (_REPO_ROOT / path).resolve()


def _available_xy_steps_pulses(goto_per_read: float) -> tuple[float, ...]:
    # A step is usable only if it changes the coarse goto-unit command.
    return tuple(
        step_p
        for step_p in STANDARD_STEP_OPTIONS_PULSES
        if round(abs(step_p) * goto_per_read) >= 1
    )


def _axis_follow_tolerance_pulses(goto_per_read: float) -> int:
    # Half a coarse goto-unit is normal read-back quantisation/noise, not a failed move.
    return max(1, math.ceil(0.5 / goto_per_read))


def _filtered_offset_samples(samples: list[float], noise_threshold_p: float) -> list[float]:
    return [sample for sample in samples if abs(sample) > noise_threshold_p]


def _average_axis_offset(samples: list[float], noise_threshold_p: float) -> float:
    usable = _filtered_offset_samples(samples, noise_threshold_p)
    if not usable:
        return 0.0
    return float(round(sum(usable) / len(usable)))


@contextlib.contextmanager
def _working_directory(path: Path):  # type: ignore[no-untyped-def]
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)

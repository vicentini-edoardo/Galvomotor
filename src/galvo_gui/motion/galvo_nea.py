"""Real galvo + neaSNOM backend using galvo_functions and nea_tools SDK."""

from __future__ import annotations

import contextlib
import ctypes
import inspect
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Tuple

import numpy as np

from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_NM,
    Z_STEP_OPTIONS_NM,
    GalvoBackend,
    GalvoError,
    SnomSample,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _REPO_ROOT / "config_files"
_DEFAULT_CAL_FILES_PATH = _CONFIG_DIR / "cal_files"
_DEFAULT_CORRECTION_FILE = "GM-2020-ftheta-10mm-fo4.tsc"

if str(_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_DIR))

try:
    import asyncio

    import nea_tools
    import nest_asyncio
    from galvo_functions import Galvo as _GalvoHW  # noqa: F401
    from galvo_functions import open_galvo as _open_galvo  # noqa: F401
    GALVO_AVAILABLE = True
except ImportError:
    GALVO_AVAILABLE = False

_N_HARMONICS = 6
# Give the galvo time to act on a move before the read-back that detects
# silently dropped moves (galvo settling is sub-ms; this covers DLL latency).
_MOVE_SETTLE_S = 0.05

# The GB511 board uses two coordinate spaces: ctr_get_current_xy_pos reports
# fine "read" pulses, while ctr_goto_xy expects coarser command units.  The
# conversion (goto units per read pulse) comes from galvo_move() in the
# working lab notebook (260220 - Galvo-Parabolic-snom-scan.ipynb, CX/CY).
# galvo_functions.Galvo.Move/GoHome omit this conversion and command targets
# ~9x out of range, railing one axis against its limit — do not use them.
_GOTO_PER_READ_X = 0.11080
_GOTO_PER_READ_Y = 0.11080


class GalvoNeaBackend(GalvoBackend):
    """Real backend: galvo_functions Galvo + neaSNOM optical signal readout.

    Requires:
      - ``galvo_functions`` module on sys.path (lab PC)
      - ``nea_tools`` + ``nest_asyncio`` installed (pip install -e ".[snom]")
      - neaSNOM server reachable at *host*

    Import-guarded: instantiation raises GalvoError if libs are missing.
    """

    def __init__(self, cal_files_path: str = str(_DEFAULT_CAL_FILES_PATH)) -> None:
        if not GALVO_AVAILABLE:
            raise GalvoError(
                "galvo_functions or nea_tools not available. "
                "Ensure galvo_functions.py is on sys.path and run: "
                "pip install nea_tools nest_asyncio"
            )
        self._cal_path = str(_resolve_repo_path(cal_files_path))
        self._connected = False
        # Duck-typed SDK/DLL handles (None until connect); Any keeps mypy out
        # of vendor attribute lookups like Galvo.Move or stream.Stream.
        self._loop: Any = None  # asyncio event loop
        self._context: Any = None  # neaspec.context (post-connect)
        self._stream_module: Any = None  # nea_tools.microscope.stream
        self._mirror_cls: Any = None
        self._galvo: Any = None  # galvo_functions.Galvo instance
        self._gb511_wrap: Any = None  # CanonGB511 low-level wrapper
        self._z0_nm: float = 0.0
        self._z_nm: float = 0.0
        self._home_x_nm: float = 0.0
        self._home_y_nm: float = 0.0

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "nea-server") -> None:
        """Connect to neaSNOM server and initialise galvo hardware."""
        self._connect_nea_session(host)
        self._open_galvo_hardware()
        self._complete_connect()

    def disconnect(self) -> None:
        should_disconnect_session = self._connected or self._loop is not None
        # ponytail: keep galvo_functions open — no close_galvo() in notebooks
        self._connected = False
        self._gb511_wrap = None
        self._mirror_cls = None
        self._stream_module = None
        self._context = None
        if should_disconnect_session:
            self._disconnect_nea_session()

    def is_connected(self) -> bool:
        return self._connected

    def _connect_nea_session(self, host: str) -> None:
        # Always create a fresh event loop for each nea_tools session.
        # Reusing or leaking the previous loop leaves the SDK half-open and
        # the next connect can hang on the microscope handshake.
        self._close_backend_loop()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        nest_asyncio.apply(self._loop)
        self._loop.run_until_complete(
            nea_tools.connect(host, fingerprint=None, path_to_dll="")
        )
        # These imports only work after nea_tools.connect has loaded the SDK.
        import neaspec  # noqa: PLC0415
        from nea_tools.microscope import stream  # noqa: PLC0415
        from nea_tools.microscope.motors import Mirror  # noqa: PLC0415
        self._context = neaspec.context
        self._stream_module = stream
        self._mirror_cls = Mirror

    def _open_galvo_hardware(self) -> None:
        # Initialise galvo hardware (independent of neaSNOM connection).
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
        with _working_directory(_CONFIG_DIR):
            self._gb511_wrap, _status = _open_galvo(**open_kwargs)
        self._galvo = _GalvoHW(self._cal_path)

    def _complete_connect(self) -> None:
        # Fail loudly now if the board rejects position reads, instead of
        # connecting into a dead board that silently ignores every move.
        self._read_gb511_bits()
        self._z0_nm = self._read_absolute_mirror_z_nm()
        self._z_nm = 0.0
        self._connected = True

    def _disconnect_nea_session(self) -> None:
        loop = self._loop
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
                        asyncio.run(disconnect_result)
        finally:
            self._close_backend_loop()

    def _close_backend_loop(self) -> None:
        loop = self._loop
        self._loop = None
        if loop is None:
            return
        with contextlib.suppress(Exception):
            current_loop = asyncio.get_event_loop()
            if current_loop is loop:
                asyncio.set_event_loop(None)
        with contextlib.suppress(Exception):
            if not loop.is_closed():
                loop.close()

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        """Move galvo by (dx_nm, dy_nm) relative to current position.

        Replicates the working notebook's scan() pattern exactly: read the
        current position in read pulses, add the displacement (K [pulse/nm]
        from the galvocal file), and command the absolute target through the
        galvo_move() unit conversion.  galvo_functions.Galvo.Move is not used:
        it is absolute-from-home (repeated jogs would not accumulate) and it
        skips the goto-unit conversion (targets land ~9x out of range).
        """
        self._require_connected()
        xb_before, yb_before = self._read_gb511_bits()
        moved = self._goto_read_units(
            xb_before + self._galvo.K * dx_nm,
            yb_before + self._galvo.K * dy_nm,
            ref=(xb_before, yb_before),
        )
        if not moved:
            return  # displacement below the goto-unit resolution
        time.sleep(_MOVE_SETTLE_S)
        xb_after, yb_after = self._read_gb511_bits()
        if (xb_after, yb_after) == (xb_before, yb_before):
            raise GalvoError(
                f"Galvo move ({dx_nm:g}, {dy_nm:g}) nm produced no motion: read-back "
                f"stayed at ({xb_before}, {yb_before}) pulses. The axis may be at its "
                "range limit — recenter with GoHome (⊙) and retry."
            )

    def move_z_relative(self, dz_nm: float) -> None:
        self._require_connected()
        with self._open_mirror() as mirror:
            mirror.go_relative(0, 0, dz_nm)
            self._wait_for_mirror(mirror)
            # Read back the hardware position so the GUI reports what the
            # mirror actually did, not what we asked for.
            z_abs_nm = float(mirror.absolute_position[2])
        self._z_nm = z_abs_nm - self._z0_nm

    def read_xy_nm(self) -> Tuple[float, float]:
        """Return galvo position from Read() as (x, y) nm."""
        self._require_connected()
        x_nm, y_nm = self._read_xy_nm_relative_to_startup_home()
        home_x_nm, home_y_nm = self._current_home_xy_nm()
        return (x_nm - home_x_nm, y_nm - home_y_nm)

    def read_z_nm(self) -> float:
        self._require_connected()
        return self._z_nm

    def set_home(self, x_nm: float | None = None, y_nm: float | None = None) -> Tuple[float, float]:
        self._require_connected()
        if (x_nm is None) != (y_nm is None):
            raise ValueError("x_nm and y_nm must be provided together.")
        if x_nm is None:
            self._home_x_nm, self._home_y_nm = self._read_xy_nm_relative_to_startup_home()
        else:
            self._home_x_nm = float(x_nm)
            self._home_y_nm = float(y_nm)
        return (self._home_x_nm, self._home_y_nm)

    def goto_center(self) -> None:
        """Move galvo back to the active home position."""
        self._require_connected()
        # Not Galvo.GoHome: it feeds read-unit pulses straight into
        # ctr_goto_xy without the goto-unit conversion (see _GOTO_PER_READ_X).
        home_x_nm, home_y_nm = self._current_home_xy_nm()
        self._goto_read_units(
            self._galvo.K * (self._galvo.X0 + home_x_nm),
            self._galvo.K * (self._galvo.Y0 + home_y_nm),
        )

    def available_xy_steps_nm(self) -> tuple[float, ...]:
        self._require_connected()
        # A step is usable only if it changes the coarser goto-unit target.
        return _available_xy_steps_nm(float(self._galvo.K) * _GOTO_PER_READ_X)

    def available_z_steps_nm(self) -> tuple[float, ...]:
        self._require_connected()
        return Z_STEP_OPTIONS_NM

    # ------------------------------------------------------------------
    # Signal readout
    # ------------------------------------------------------------------

    def read_sample(self, t_integ_s: float = 0.05) -> SnomSample:
        """Read optical amplitude and phase via neaSNOM stream (averaged over t_integ_s)."""
        self._require_connected()
        keys = [f"O{h}A" for h in range(_N_HARMONICS)] + [f"O{h}P" for h in range(_N_HARMONICS)]
        totals = {k: 0.0 for k in keys}
        counts = {k: 0 for k in keys}

        with self._stream_module.Stream() as s:
            t_end = time.time() + t_integ_s
            while time.time() < t_end:
                for k in keys:
                    try:
                        v = float(s.data[k][-1])
                        if v != 0.0:
                            totals[k] += v
                            counts[k] += 1
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(0.02)

        def _get(k: str) -> float:
            return totals[k] / counts[k] if counts[k] else float("nan")

        xy = self.read_xy_nm()
        return SnomSample(
            xy_nm=xy,
            o_amp=np.array([_get(f"O{h}A") for h in range(_N_HARMONICS)]),
            o_phase=np.array([_get(f"O{h}P") for h in range(_N_HARMONICS)]),
        )

    # ------------------------------------------------------------------
    # Scan — replicates notebook scan_galvo() pattern
    # ------------------------------------------------------------------

    def scan(
        self,
        dx_nm: float,
        dy_nm: float,
        nb_x: int,
        nb_y: int,
        twait: float,
        t_integ_s: float,
        on_point: Callable[[int, int, SnomSample], None],
        stop_check: Callable[[], bool],
    ) -> None:
        """Raster scan. Mirrors notebook scan_galvo() with galvo.Read() for coordinates."""
        self._require_connected()

        step_x = dx_nm / (nb_x - 1) if nb_x > 1 else 0.0
        step_y = dy_nm / (nb_y - 1) if nb_y > 1 else 0.0

        # Move to start corner (-dx/2, -dy/2) relative to current centre.
        self.move_relative(-dx_nm / 2.0, -dy_nm / 2.0)
        time.sleep(twait)
        x_start, y_start = self.read_xy_nm()  # actual position after quantisation

        for iy in range(nb_y):
            for ix in range(nb_x):
                if stop_check():
                    break

                sample = self.read_sample(t_integ_s)
                on_point(ix, iy, sample)

                if ix < nb_x - 1:
                    self.move_relative(step_x, 0.0)
                    time.sleep(twait)

            if stop_check():
                break

            if iy < nb_y - 1:
                # End of row: correct back to x_start, step y down.
                x_curr, _ = self.read_xy_nm()
                self.move_relative(x_start - x_curr, step_y)
                time.sleep(twait)

        # Return to centre.
        self.goto_center()

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("GalvoNeaBackend is not connected.")

    # ------------------------------------------------------------------
    # GB511 access
    # ------------------------------------------------------------------

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

    def _read_xy_nm_relative_to_startup_home(self) -> Tuple[float, float]:
        xb, yb = self._read_gb511_bits()
        x_nm = self._galvo.Bit2Pos(xb) - self._galvo.X0
        y_nm = self._galvo.Bit2Pos(yb) - self._galvo.Y0
        return (float(x_nm), float(y_nm))

    def _current_home_xy_nm(self) -> Tuple[float, float]:
        return (float(getattr(self, "_home_x_nm", 0.0)), float(getattr(self, "_home_y_nm", 0.0)))

    def _goto_read_units(
        self,
        x_read: float,
        y_read: float,
        ref: Tuple[int, int] | None = None,
    ) -> bool:
        """Command an absolute target given in read pulses (notebook galvo_move).

        Returns False when the target quantises onto the goto-unit position of
        *ref* (the current position, read if not supplied), i.e. the request is
        below the board's command resolution and no motion can be expected.
        """
        gx = int(_GOTO_PER_READ_X * x_read)
        gy = int(_GOTO_PER_READ_Y * y_read)
        xb_now, yb_now = ref if ref is not None else self._read_gb511_bits()
        self._call_gb511("ctr_goto_xy", gx, gy)
        return (gx, gy) != (
            int(_GOTO_PER_READ_X * xb_now),
            int(_GOTO_PER_READ_Y * yb_now),
        )

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


def _available_xy_steps_nm(k_bit_per_nm: float) -> tuple[float, ...]:
    return tuple(
        step_nm
        for step_nm in STANDARD_STEP_OPTIONS_NM
        if round(abs(step_nm) * k_bit_per_nm) >= 1
    )


@contextlib.contextmanager
def _working_directory(path: Path):  # type: ignore[no-untyped-def]
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)

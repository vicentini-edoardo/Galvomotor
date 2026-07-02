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
# Give the galvo time to act on a Move before the read-back that detects
# silently dropped moves (galvo settling is sub-ms; this covers DLL latency).
_MOVE_SETTLE_S = 0.05


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

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "nea-server") -> None:
        """Connect to neaSNOM server and initialise galvo hardware."""
        # Always create a fresh event loop to avoid reusing a closed loop
        # from a previous session (see Andor nea_snom.py:55-61).
        with contextlib.suppress(Exception):
            old_loop = asyncio.get_event_loop()
            if not old_loop.is_closed():
                old_loop.close()
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

        # Initialise galvo hardware (independent of neaSNOM connection).
        with _working_directory(_CONFIG_DIR):
            self._gb511_wrap, _status = _open_galvo(CalFn=_DEFAULT_CORRECTION_FILE)
        self._galvo = _GalvoHW(self._cal_path)
        # Fail loudly now if the board rejects position reads, instead of
        # connecting into a dead board that silently ignores every move.
        self._read_gb511_bits()
        self._z0_nm = self._read_absolute_mirror_z_nm()
        self._z_nm = 0.0
        self._connected = True

    def disconnect(self) -> None:
        was_connected = self._connected
        # ponytail: keep galvo_functions open — no close_galvo() in notebooks
        self._connected = False
        self._gb511_wrap = None
        self._mirror_cls = None
        if was_connected:
            with contextlib.suppress(Exception):
                disconnect_result = nea_tools.disconnect()
                if inspect.isawaitable(disconnect_result) and self._loop is not None:
                    self._loop.run_until_complete(disconnect_result)

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        """Move galvo by (dx_nm, dy_nm) relative to current position."""
        self._require_connected()
        # Motion must go through galvo_functions.Galvo.Move — the vendor
        # wrapper the lab notebooks raster with.  Calling ctr_goto_xy on the
        # raw DLL with a guessed prototype mis-routes the axis arguments:
        # on hardware both X and Y jogs drove the same mirror with
        # inconsistent step sizes.  Move's Xmax guard can still silently
        # drop a move, so verify with a position read-back instead of
        # bypassing the vendor call path.
        expected_dx_bits = round(self._galvo.K * dx_nm)
        expected_dy_bits = round(self._galvo.K * dy_nm)
        xb_before, yb_before = self._read_gb511_bits()
        try:
            self._galvo.Move(dx_nm, dy_nm, self._gb511_wrap)
        except GalvoError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GalvoError(f"Galvo.Move failed: {exc}") from exc
        if expected_dx_bits == 0 and expected_dy_bits == 0:
            return
        time.sleep(_MOVE_SETTLE_S)
        xb_after, yb_after = self._read_gb511_bits()
        if (xb_after, yb_after) == (xb_before, yb_before):
            raise GalvoError(
                f"Galvo.Move({dx_nm:g}, {dy_nm:g}) produced no motion: bits stayed at "
                f"({xb_before}, {yb_before}), expected a change of about "
                f"({expected_dx_bits:+d}, {expected_dy_bits:+d}) bits. "
                "galvo_functions' Xmax guard may have dropped the move — "
                "recenter with GoHome (⊙) and retry."
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
        xb, yb = self._read_gb511_bits()
        x_nm = self._galvo.Bit2Pos(xb) - self._galvo.X0
        y_nm = self._galvo.Bit2Pos(yb) - self._galvo.Y0
        return (float(x_nm), float(y_nm))

    def read_z_nm(self) -> float:
        self._require_connected()
        return self._z_nm

    def goto_center(self) -> None:
        """Move galvo back to centre (0, 0)."""
        self._require_connected()
        self._galvo.GoHome(self._gb511_wrap)

    def available_xy_steps_nm(self) -> tuple[float, ...]:
        self._require_connected()
        return _available_xy_steps_nm(float(self._galvo.K))

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

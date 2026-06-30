"""Real galvo + neaSNOM backend using galvo_functions and nea_tools SDK."""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, Tuple

try:
    import asyncio

    import nea_tools
    import nest_asyncio
    from galvo_functions import Galvo as _GalvoHW  # noqa: F401
    from galvo_functions import open_galvo as _open_galvo  # noqa: F401
    GALVO_AVAILABLE = True
except ImportError:
    GALVO_AVAILABLE = False

import numpy as np

from galvo_gui.motion.base import GalvoBackend, GalvoError, SnomSample

_N_HARMONICS = 6


class GalvoNeaBackend(GalvoBackend):
    """Real backend: galvo_functions Galvo + neaSNOM optical signal readout.

    Requires:
      - ``galvo_functions`` module on sys.path (lab PC)
      - ``nea_tools`` + ``nest_asyncio`` installed (pip install -e ".[snom]")
      - neaSNOM server reachable at *host*

    Import-guarded: instantiation raises GalvoError if libs are missing.
    """

    def __init__(self, cal_files_path: str = "galvomotor/cal_files") -> None:
        if not GALVO_AVAILABLE:
            raise GalvoError(
                "galvo_functions or nea_tools not available. "
                "Ensure galvo_functions.py is on sys.path and run: "
                "pip install nea_tools nest_asyncio"
            )
        self._cal_path = cal_files_path
        self._connected = False
        self._loop: Any | None = None  # asyncio event loop
        self._context: Any | None = None  # neaspec.context (post-connect)
        self._stream_module: Any | None = None  # nea_tools.microscope.stream
        self._galvo: Any | None = None  # galvo_functions.Galvo instance

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
        self._context = neaspec.context
        self._stream_module = stream

        # Initialise galvo hardware (independent of neaSNOM connection).
        _open_galvo()
        self._galvo = _GalvoHW(self._cal_path)
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            with contextlib.suppress(Exception):
                nea_tools.disconnect()
        # ponytail: keep galvo_functions open — no close_galvo() in notebooks
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        """Move galvo by (dx_nm, dy_nm) relative to current position."""
        self._require_connected()
        self._galvo.Move(dx_nm, dy_nm)

    def read_xy_nm(self) -> Tuple[float, float]:
        """Return galvo position from Read() as (x, y) nm."""
        self._require_connected()
        pos = self._galvo.Read()  # returns (x, y)
        return (float(pos[0]), float(pos[1]))

    def goto_center(self) -> None:
        """Move galvo back to centre (0, 0)."""
        self._require_connected()
        x, y = self.read_xy_nm()
        self._galvo.Move(-x, -y)

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

        step_x = dx_nm / nb_x
        step_y = dy_nm / nb_y

        # Move to start corner (-dx/2, -dy/2) relative to current centre.
        self._galvo.Move(-dx_nm / 2.0, -dy_nm / 2.0)
        time.sleep(twait)
        x_start, y_start = self.read_xy_nm()  # actual position after quantisation

        for iy in range(nb_y):
            for ix in range(nb_x):
                if stop_check():
                    break

                sample = self.read_sample(t_integ_s)
                on_point(ix, iy, sample)

                if ix < nb_x - 1:
                    self._galvo.Move(step_x, 0.0)
                    time.sleep(twait)

            if stop_check():
                break

            # End of row: correct back to x_start, step y down.
            x_curr, _ = self.read_xy_nm()
            self._galvo.Move(x_start - x_curr, step_y)
            time.sleep(twait)

        # Return to centre.
        self.goto_center()

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("GalvoNeaBackend is not connected.")

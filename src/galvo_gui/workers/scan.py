"""ScanWorker: runs a galvo raster scan in a background QThread."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from galvo_gui.motion.base import (
    GalvoBackend,
    GalvoError,
    NeaBackend,
    SnomSample,
    run_raster_scan,
)

_N_HARMONICS = 6


class ScanWorker(QThread):
    """Execute a 2-D galvo raster scan and save results to HDF5.

    Usage::

        worker = ScanWorker(galvo, nea, params)
        worker.point_done.connect(panel.on_point_done)
        worker.scan_finished.connect(panel.on_scan_finished)
        worker.start()
        # ...
        worker.stop()   # request cooperative stop
        worker.wait()   # join thread

    Signals:
        point_done(ix, iy, o_amp(6,), o_phase(6,)): one pixel complete
        progress(done, total): pixel count update
        scan_finished(path): scan done, HDF5 saved to *path*
        error(str): unrecoverable error message
        log_message(str): informational message
    """

    point_done = pyqtSignal(int, int, object, object)   # ix, iy, amp(6,), phase(6,)
    progress = pyqtSignal(int, int)                      # done, total
    scan_finished = pyqtSignal(str)                      # saved path
    error = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(
        self,
        galvo: GalvoBackend,
        nea: NeaBackend,
        dx_pulses: float,
        dy_pulses: float,
        nb_x: int,
        nb_y: int,
        twait: float,
        t_integ_s: float,
        save_dir: str,
        filename: str,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._galvo = galvo
        self._nea = nea
        self._dx_pulses = dx_pulses
        self._dy_pulses = dy_pulses
        self._nb_x = nb_x
        self._nb_y = nb_y
        self._twait = twait
        self._t_integ_s = t_integ_s
        self._save_dir = save_dir
        self._filename = filename
        self._stop_flag = False
        self._t0: float = 0.0

        # Accumulation arrays filled by _on_point callback
        self._amp = np.full((_N_HARMONICS, nb_y, nb_x), float("nan"))
        self._phase = np.full((_N_HARMONICS, nb_y, nb_x), float("nan"))
        self._coords = np.full((nb_y, nb_x, 2), float("nan"))
        self._coords_pulses = np.full((nb_y, nb_x, 2), float("nan"))

    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request cooperative stop (checked between pixels)."""
        self._stop_flag = True

    def run(self) -> None:
        self._t0 = time.monotonic()
        total = self._nb_x * self._nb_y
        self._done = 0

        def _on_point(ix: int, iy: int, sample: SnomSample) -> None:
            self._amp[:, iy, ix] = sample.o_amp
            self._phase[:, iy, ix] = sample.o_phase
            self._coords[iy, ix, 0] = sample.xy_nm[0]
            self._coords[iy, ix, 1] = sample.xy_nm[1]
            self._coords_pulses[iy, ix, 0] = sample.xy_pulses[0]
            self._coords_pulses[iy, ix, 1] = sample.xy_pulses[1]
            self._done += 1
            self.point_done.emit(ix, iy, sample.o_amp.copy(), sample.o_phase.copy())
            self.progress.emit(self._done, total)

        try:
            self.log_message.emit(
                f"Scan started: {self._nb_x}×{self._nb_y} px, "
                f"Δx={self._dx_pulses:.0f} pulses, Δy={self._dy_pulses:.0f} pulses, "
                f"twait={self._twait:.2f} s"
            )
            run_raster_scan(
                self._galvo,
                self._nea,
                self._dx_pulses,
                self._dy_pulses,
                self._nb_x,
                self._nb_y,
                self._twait,
                self._t_integ_s,
                _on_point,
                lambda: self._stop_flag,
            )
        except GalvoError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {exc}")
            return
        finally:
            elapsed = time.monotonic() - self._t0
            self.log_message.emit(
                f"Scan {'stopped' if self._stop_flag else 'complete'} "
                f"after {elapsed:.1f} s ({self._done}/{total} px)"
            )

        # Save results
        path = self._save()
        if path:
            self.scan_finished.emit(path)

    # ------------------------------------------------------------------

    def _save(self) -> str:
        """Write results to disk. Returns HDF5 path or '' on failure."""
        from galvo_gui.io.save import save_scan_h5, save_scan_text  # avoid circular at import time

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = self._filename if self._filename.endswith(".h5") else f"{self._filename}.h5"
        # Insert timestamp before extension to avoid overwrite
        stem = fname[:-3]
        path = Path(self._save_dir) / f"{stem}_{now}.h5"
        text_path = path.with_suffix(".txt")

        meta = {
            "dx_pulses": self._dx_pulses,
            "dy_pulses": self._dy_pulses,
            "nb_x": self._nb_x,
            "nb_y": self._nb_y,
            "twait_s": self._twait,
            "t_integ_s": self._t_integ_s,
            "timestamp": now,
            "pixels_acquired": int(self._done),
        }

        try:
            save_scan_h5(path, self._amp, self._phase, self._coords, self._coords_pulses, meta)
            save_scan_text(text_path, self._amp, self._phase, self._coords_pulses, meta)
            self.log_message.emit(f"Saved: {path}")
            self.log_message.emit(f"Saved: {text_path}")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Save failed: {exc}")
            return ""

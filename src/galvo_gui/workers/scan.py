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
    """Execute a 2-D (or 3-D, with a Z stack) galvo raster scan and save to HDF5.

    A 3-D scan is the same XY raster repeated once per Z slice: Z is moved
    with the neaSNOM mirror (already used by the Motion tab's Z jog) between
    slices, centred on the Z position where the scan was started.

    Usage::

        worker = ScanWorker(galvo, nea, params)
        worker.point_done.connect(panel.on_point_done)
        worker.scan_finished.connect(panel.on_scan_finished)
        worker.start()
        # ...
        worker.stop()   # request cooperative stop
        worker.wait()   # join thread

    Signals:
        point_done(ix, iy, iz, o_amp(6,), o_phase(6,)): one pixel complete
        progress(done, total): pixel count update
        scan_finished(path): scan done, HDF5 saved to *path*
        error(str): unrecoverable error message
        log_message(str): informational message
    """

    point_done = pyqtSignal(int, int, int, object, object)  # ix, iy, iz, amp(6,), phase(6,)
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
        nb_z: int = 1,
        dz_nm: float = 0.0,
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
        self._nb_z = max(1, nb_z)
        self._dz_nm = dz_nm
        self._stop_flag = False
        self._t0: float = 0.0

        # Accumulation arrays filled by _on_point callback
        self._amp = np.full((_N_HARMONICS, self._nb_z, nb_y, nb_x), float("nan"))
        self._phase = np.full((_N_HARMONICS, self._nb_z, nb_y, nb_x), float("nan"))
        self._coords = np.full((self._nb_z, nb_y, nb_x, 2), float("nan"))
        self._coords_pulses = np.full((self._nb_z, nb_y, nb_x, 2), float("nan"))
        self._coords_z = np.full((self._nb_z,), float("nan"))

    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request cooperative stop (checked between pixels)."""
        self._stop_flag = True

    def run(self) -> None:
        self._t0 = time.monotonic()
        nb_z = self._nb_z
        total = self._nb_x * self._nb_y * nb_z
        self._done = 0
        z_moved_nm = 0.0  # net Z motion applied so far, for stop/error unwind

        def _make_on_point(iz: int):
            def _on_point(ix: int, iy: int, sample: SnomSample) -> None:
                self._amp[:, iz, iy, ix] = sample.o_amp
                self._phase[:, iz, iy, ix] = sample.o_phase
                self._coords[iz, iy, ix, 0] = sample.xy_nm[0]
                self._coords[iz, iy, ix, 1] = sample.xy_nm[1]
                self._coords_pulses[iz, iy, ix, 0] = sample.xy_pulses[0]
                self._coords_pulses[iz, iy, ix, 1] = sample.xy_pulses[1]
                self._done += 1
                self.point_done.emit(ix, iy, iz, sample.o_amp.copy(), sample.o_phase.copy())
                self.progress.emit(self._done, total)

            return _on_point

        try:
            if nb_z > 1:
                self.log_message.emit(
                    f"Scan started: {self._nb_x}×{self._nb_y}×{nb_z} px, "
                    f"Δx={self._dx_pulses:.0f} pulses, Δy={self._dy_pulses:.0f} pulses, "
                    f"Δz={self._dz_nm:.0f} nm × {nb_z}, twait={self._twait:.2f} s"
                )
                # Centre the Z stack on the current focus: start at the bottom.
                half_span = self._dz_nm * (nb_z - 1) / 2.0
                self._nea.move_z_relative(-half_span)
                z_moved_nm -= half_span
            else:
                self.log_message.emit(
                    f"Scan started: {self._nb_x}×{self._nb_y} px, "
                    f"Δx={self._dx_pulses:.0f} pulses, Δy={self._dy_pulses:.0f} pulses, "
                    f"twait={self._twait:.2f} s"
                )

            for iz in range(nb_z):
                if self._stop_flag:
                    break
                self._coords_z[iz] = self._nea.read_z_nm()
                run_raster_scan(
                    self._galvo,
                    self._nea,
                    self._dx_pulses,
                    self._dy_pulses,
                    self._nb_x,
                    self._nb_y,
                    self._twait,
                    self._t_integ_s,
                    _make_on_point(iz),
                    lambda: self._stop_flag,
                )
                if iz < nb_z - 1 and not self._stop_flag:
                    self._nea.move_z_relative(self._dz_nm)
                    z_moved_nm += self._dz_nm
        except GalvoError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {exc}")
            return
        finally:
            # Return Z to where the scan started, whatever happened above.
            if z_moved_nm:
                import contextlib
                with contextlib.suppress(Exception):
                    self._nea.move_z_relative(-z_moved_nm)
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
        pulses_per_nm = float(self._galvo.pulses_per_nm())
        nm_per_pulse = 1.0 / pulses_per_nm if pulses_per_nm else float("inf")

        is_3d = self._nb_z > 1
        meta = {
            "scan_mode": "3D" if is_3d else "2D",
            "dx_pulses": self._dx_pulses,
            "dy_pulses": self._dy_pulses,
            "nb_x": self._nb_x,
            "nb_y": self._nb_y,
            "twait_s": self._twait,
            "t_integ_s": self._t_integ_s,
            "timestamp": now,
            "pixels_acquired": int(self._done),
            "position_conversion_factor_parameters": {
                "pulses_per_nm": pulses_per_nm,
                "nm_per_pulse": nm_per_pulse,
                "x_nm_expression": f"x_nm = x_pulse / {pulses_per_nm:.12g}",
                "y_nm_expression": f"y_nm = y_pulse / {pulses_per_nm:.12g}",
            },
        }
        if is_3d:
            meta["nb_z"] = self._nb_z
            meta["dz_nm"] = self._dz_nm
            meta["z_range_nm"] = self._dz_nm * (self._nb_z - 1)
            meta["z_slices_nm"] = self._coords_z.tolist()

        # 2-D scans keep the original (H, ny, nx) shape on disk; a 3-D scan
        # (nb_z > 1) writes (H, nz, ny, nx) — same arrays, squeeze is a no-op
        # data-wise, it just drops the singleton z axis for 2-D compatibility.
        amp = self._amp if is_3d else self._amp[:, 0]
        phase = self._phase if is_3d else self._phase[:, 0]
        coords = self._coords if is_3d else self._coords[0]
        coords_pulses = self._coords_pulses if is_3d else self._coords_pulses[0]
        coords_z = self._coords_z if is_3d else None

        try:
            save_scan_h5(path, amp, phase, coords, coords_pulses, meta, coords_z=coords_z)
            save_scan_text(text_path, amp, phase, coords_pulses, meta, coords_z=coords_z)
            self.log_message.emit(f"Saved: {path}")
            self.log_message.emit(f"Saved: {text_path}")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Save failed: {exc}")
            return ""

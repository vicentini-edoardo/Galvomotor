"""Scan panel: raster scan parameters, live image, and save controls."""

from __future__ import annotations

import os

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from galvo_gui.gui import theme
from galvo_gui.gui.widgets import LogView
from galvo_gui.motion.base import GalvoBackend, NeaBackend
from galvo_gui.workers.scan import ScanWorker

_N_HARMONICS = 6


class ScanPanel(QWidget):
    """2-D raster scan panel (Tab 2).

    Receives a galvo backend (XY motion) and a neaSNOM backend (optical
    readout) from the Connection tab. A scan needs both. Drives a ScanWorker,
    displays live amplitude/phase images, and saves results to HDF5.

    Signals:
        log_message(str): forwarded to status bar
        running_changed(bool): True when scan is active
    """

    log_message = pyqtSignal(str)
    running_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._galvo_backend: GalvoBackend | None = None
        self._nea_backend: NeaBackend | None = None
        self._worker: ScanWorker | None = None
        self._settings = QSettings("galvo_gui", "ScanPanel")

        # Accumulation grids — resized when scan starts
        self._amp_grid: np.ndarray | None = None    # (nb_y, nb_x)
        self._phase_grid: np.ndarray | None = None  # (nb_y, nb_x)

        self._build_ui()
        self._restore_settings()
        self._set_started(False)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left: controls
        left = QWidget()
        left.setMaximumWidth(320)
        lv = QVBoxLayout(left)
        lv.setSpacing(8)
        lv.addWidget(self._build_params_group())
        lv.addWidget(self._build_save_group())
        lv.addWidget(self._build_display_group())
        lv.addWidget(self._build_control_group())
        self._log = LogView(left)
        lv.addWidget(self._log)
        lv.addStretch()

        # Right: live image
        self._glw = pg.GraphicsLayoutWidget()
        theme.style_graphics_layout(self._glw)
        self._plot = self._glw.addPlot(title="Scan image")
        self._img_item = pg.ImageItem()
        self._plot.addItem(self._img_item)
        self._plot.setAspectLocked(True)
        self._plot.setLabel("bottom", "x (px)")
        self._plot.setLabel("left", "y (px)")
        self._colorbar = pg.ColorBarItem(
            values=(0, 1),
            colorMap="viridis",
            label="Signal",
        )
        self._colorbar.setImageItem(self._img_item)

        splitter.addWidget(left)
        splitter.addWidget(self._glw)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

    def _build_params_group(self) -> QGroupBox:
        grp = QGroupBox("Scan Parameters")
        grid = QGridLayout(grp)
        grid.setColumnStretch(1, 1)

        row = 0
        grid.addWidget(QLabel("X range (pulses):"), row, 0)
        self._x_range_pulses = QDoubleSpinBox()
        self._x_range_pulses.setRange(1.0, 1_000_000.0)
        self._x_range_pulses.setValue(500.0)
        self._x_range_pulses.setDecimals(0)
        self._x_range_pulses.setSingleStep(100.0)
        grid.addWidget(self._x_range_pulses, row, 1)

        row += 1
        grid.addWidget(QLabel("Y range (pulses):"), row, 0)
        self._y_range_pulses = QDoubleSpinBox()
        self._y_range_pulses.setRange(1.0, 1_000_000.0)
        self._y_range_pulses.setValue(500.0)
        self._y_range_pulses.setDecimals(0)
        self._y_range_pulses.setSingleStep(100.0)
        grid.addWidget(self._y_range_pulses, row, 1)

        row += 1
        grid.addWidget(QLabel("X pixels:"), row, 0)
        self._nb_x = QSpinBox()
        self._nb_x.setRange(2, 1000)
        self._nb_x.setValue(20)
        grid.addWidget(self._nb_x, row, 1)

        row += 1
        grid.addWidget(QLabel("Y pixels:"), row, 0)
        self._nb_y = QSpinBox()
        self._nb_y.setRange(2, 1000)
        self._nb_y.setValue(20)
        grid.addWidget(self._nb_y, row, 1)

        row += 1
        grid.addWidget(QLabel("Step time (s):"), row, 0)
        self._twait = QDoubleSpinBox()
        self._twait.setRange(0.0, 60.0)
        self._twait.setValue(0.2)
        self._twait.setDecimals(3)
        self._twait.setSingleStep(0.05)
        grid.addWidget(self._twait, row, 1)

        row += 1
        grid.addWidget(QLabel("Integ time (s):"), row, 0)
        self._t_integ = QDoubleSpinBox()
        self._t_integ.setRange(0.01, 10.0)
        self._t_integ.setValue(0.05)
        self._t_integ.setDecimals(3)
        self._t_integ.setSingleStep(0.01)
        grid.addWidget(self._t_integ, row, 1)

        return grp

    def _build_save_group(self) -> QGroupBox:
        grp = QGroupBox("Save")
        grid = QGridLayout(grp)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Directory:"), 0, 0)
        self._save_dir = QLineEdit(os.path.expanduser("~"))
        grid.addWidget(self._save_dir, 0, 1)
        btn = QPushButton("…")
        btn.setFixedWidth(30)
        btn.clicked.connect(self._browse_dir)
        grid.addWidget(btn, 0, 2)

        grid.addWidget(QLabel("Filename:"), 1, 0)
        self._filename = QLineEdit("galvo_scan")
        grid.addWidget(self._filename, 1, 1, 1, 2)

        return grp

    def _build_display_group(self) -> QGroupBox:
        grp = QGroupBox("Display")
        hbox = QHBoxLayout(grp)

        hbox.addWidget(QLabel("Harmonic:"))
        self._harmonic_combo = QComboBox()
        for h in range(_N_HARMONICS):
            self._harmonic_combo.addItem(f"O{h}")
        self._harmonic_combo.setCurrentIndex(2)  # O2 default like notebooks
        self._harmonic_combo.currentIndexChanged.connect(self._update_image)
        hbox.addWidget(self._harmonic_combo)

        hbox.addWidget(QLabel("Show:"))
        self._data_combo = QComboBox()
        self._data_combo.addItem("Amplitude")
        self._data_combo.addItem("Phase")
        self._data_combo.currentIndexChanged.connect(self._update_image)
        hbox.addWidget(self._data_combo)

        return grp

    def _build_control_group(self) -> QGroupBox:
        grp = QGroupBox("Control")
        vbox = QVBoxLayout(grp)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Scan")
        self._start_btn.setProperty("accent", True)
        self._start_btn.clicked.connect(self._start_scan)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._stop_scan)
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)

        vbox.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        vbox.addWidget(self._progress)

        self._progress_label = QLabel("0 / 0 px")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._progress_label)

        return grp

    # ------------------------------------------------------------------
    # Public slots
    # ------------------------------------------------------------------

    def set_galvo_backend(self, backend: GalvoBackend) -> None:
        self._galvo_backend = backend
        self._update_start_enabled()

    def clear_galvo_backend(self) -> None:
        self._galvo_backend = None
        self._update_start_enabled()

    def set_nea_backend(self, backend: NeaBackend) -> None:
        self._nea_backend = backend
        self._update_start_enabled()

    def clear_nea_backend(self) -> None:
        self._nea_backend = None
        self._update_start_enabled()

    def _both_connected(self) -> bool:
        return self._galvo_backend is not None and self._nea_backend is not None

    def _update_start_enabled(self) -> None:
        running = self._worker is not None
        self._start_btn.setEnabled(self._both_connected() and not running)

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select save directory",
                                                self._save_dir.text())
        if path:
            self._save_dir.setText(path)

    def _start_scan(self) -> None:
        if self._galvo_backend is None or self._nea_backend is None:
            self._log.append_line(
                "ERROR: a scan needs both the galvo and neaSNOM connected."
            )
            return

        nb_x = self._nb_x.value()
        nb_y = self._nb_y.value()

        # Fresh accumulation grids (NaN until pixel received)
        self._amp_grid = np.full((nb_y, nb_x), float("nan"))
        self._phase_grid = np.full((nb_y, nb_x), float("nan"))

        # Clear image
        self._img_item.setImage(np.zeros((nb_x, nb_y), dtype=np.float32))

        self._worker = ScanWorker(
            galvo=self._galvo_backend,
            nea=self._nea_backend,
            dx_pulses=self._x_range_pulses.value(),
            dy_pulses=self._y_range_pulses.value(),
            nb_x=nb_x,
            nb_y=nb_y,
            twait=self._twait.value(),
            t_integ_s=self._t_integ.value(),
            save_dir=self._save_dir.text(),
            filename=self._filename.text(),
        )
        self._worker.point_done.connect(self._on_point_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log_message.connect(self._log.append_line)
        self._worker.log_message.connect(self.log_message)
        self._worker.finished.connect(self._on_worker_finished)

        self._progress.setRange(0, nb_x * nb_y)
        self._progress.setValue(0)
        self._progress_label.setText(f"0 / {nb_x * nb_y} px")

        self._set_started(True)
        self._worker.start()

    def _stop_scan(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._stop_btn.setEnabled(False)
            self._stop_btn.setText("Stopping…")

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_point_done(self, ix: int, iy: int, o_amp: object, o_phase: object) -> None:
        # o_amp / o_phase are numpy arrays (shape 6,) carried as object in signal
        amp_arr: np.ndarray = o_amp  # type: ignore[assignment]
        phase_arr: np.ndarray = o_phase  # type: ignore[assignment]
        h = self._harmonic_combo.currentIndex()

        if self._amp_grid is not None:
            self._amp_grid[iy, ix] = float(amp_arr[h])
        if self._phase_grid is not None:
            self._phase_grid[iy, ix] = float(phase_arr[h])

        self._update_image()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress.setValue(done)
        self._progress_label.setText(f"{done} / {total} px")

    def _on_scan_finished(self, path: str) -> None:
        self._log.append_line(f"Scan saved → {path}")

    def _on_error(self, msg: str) -> None:
        self._log.append_line(f"ERROR: {msg}")
        self.log_message.emit(f"Scan error: {msg}")

    def _on_worker_finished(self) -> None:
        self._set_started(False)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    # ------------------------------------------------------------------
    # Image update
    # ------------------------------------------------------------------

    def _update_image(self) -> None:
        h = self._harmonic_combo.currentIndex()
        use_phase = self._data_combo.currentIndex() == 1

        grid = self._phase_grid if use_phase else self._amp_grid
        if grid is None:
            return

        # Replace NaN with 0 for display
        display = np.where(np.isfinite(grid), grid, 0.0)

        # pyqtgraph ImageItem: row=y, col=x → transpose for (nx, ny) convention
        self._img_item.setImage(display.T.astype(np.float32), autoLevels=True)

        # Update colorbar label
        lbl = f"O{h} {'Phase (rad)' if use_phase else 'Amplitude'}"
        self._colorbar.setLabel(lbl)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _set_started(self, started: bool) -> None:
        self._start_btn.setEnabled(not started and self._both_connected())
        self._stop_btn.setEnabled(started)
        if not started:
            self._stop_btn.setText("Stop")
        self.running_changed.emit(started)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _restore_settings(self) -> None:
        import contextlib
        s = self._settings
        for attr, key, default in [
            (self._twait, "twait", 0.2),
            (self._t_integ, "t_integ", 0.05),
        ]:
            v = s.value(key, default)
            with contextlib.suppress(Exception):
                attr.setValue(float(v))  # type: ignore[union-attr]
        for attr, key, default in [
            (self._x_range_pulses, "x_range_pulses", 500.0),
            (self._y_range_pulses, "y_range_pulses", 500.0),
        ]:
            v = s.value(key, default)
            with contextlib.suppress(Exception):
                attr.setValue(float(v))  # type: ignore[union-attr]
        for attr, key, default in [
            (self._nb_x, "nb_x", 20),
            (self._nb_y, "nb_y", 20),
        ]:
            v = s.value(key, default)
            with contextlib.suppress(Exception):
                attr.setValue(int(v))  # type: ignore[union-attr]
        sd = s.value("save_dir", os.path.expanduser("~"))
        if isinstance(sd, str):
            self._save_dir.setText(sd)
        fn = s.value("filename", "galvo_scan")
        if isinstance(fn, str):
            self._filename.setText(fn)

    def save_settings(self) -> None:
        s = self._settings
        s.setValue("x_range_pulses", self._x_range_pulses.value())
        s.setValue("y_range_pulses", self._y_range_pulses.value())
        s.setValue("nb_x", self._nb_x.value())
        s.setValue("nb_y", self._nb_y.value())
        s.setValue("twait", self._twait.value())
        s.setValue("t_integ", self._t_integ.value())
        s.setValue("save_dir", self._save_dir.text())
        s.setValue("filename", self._filename.text())

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(3000)
        super().closeEvent(event)  # type: ignore[misc]

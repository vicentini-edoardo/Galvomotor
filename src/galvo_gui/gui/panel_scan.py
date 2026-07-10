"""Scan panel: raster scan parameters, live image, and save controls."""

from __future__ import annotations

import os

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from galvo_gui.gui import theme
from galvo_gui.gui.widgets import LogView
from galvo_gui.motion.base import Z_STEP_OPTIONS_NM, GalvoBackend, NeaBackend
from galvo_gui.workers.scan import ScanWorker

_N_HARMONICS = 6


class ScanPanel(QWidget):
    """Raster scan panel (Tab 2) — 2-D (X,Y) or 3-D (X,Y,Z) with a Z stack.

    Receives a galvo backend (XY motion) and a neaSNOM backend (optical
    readout + Z motion) from the Connection tab. A scan needs both. Drives a
    ScanWorker, displays a live amplitude/phase image (with a Z-slice picker
    and max-projection in 3-D mode), and saves results to HDF5.

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

        # Accumulation grids — resized when scan starts. Always 3-D internally
        # (nb_z, nb_y, nb_x); a 2-D scan is just nb_z == 1.
        self._amp_grid: np.ndarray | None = None    # (nb_z, nb_y, nb_x)
        self._phase_grid: np.ndarray | None = None  # (nb_z, nb_y, nb_x)

        self._build_ui()
        self._restore_settings()
        self._on_3d_toggled(self._z_enable.isChecked())
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
        self._t_integ.setRange(0.02, 10.0)
        self._t_integ.setValue(0.05)
        self._t_integ.setDecimals(3)
        self._t_integ.setSingleStep(0.01)
        grid.addWidget(self._t_integ, row, 1)

        row += 1
        self._z_enable = QCheckBox("3D scan (Z stack)")
        self._z_enable.toggled.connect(self._on_3d_toggled)
        grid.addWidget(self._z_enable, row, 0, 1, 2)

        row += 1
        self._z_cluster = self._build_z_cluster()
        grid.addWidget(self._z_cluster, row, 0, 1, 2)

        return grp

    def _build_z_cluster(self) -> QWidget:
        """Nested Z-stack sub-panel — same 'cluster' look as the Motion tab's
        neaSNOM Z group, so the 3-D params read as one unit, not more rows
        bleeding into the X/Y scan params above them."""
        cluster = QWidget()
        cluster.setObjectName("MotionCluster")
        outer = QVBoxLayout(cluster)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        title = QLabel("Z Stack")
        title.setObjectName("MotionClusterTitle")
        outer.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)

        step_label = QLabel("Step (nm):")
        step_label.setObjectName("MotionInlineLabel")
        grid.addWidget(step_label, 0, 0)
        self._z_step_combo = QComboBox()
        for step in Z_STEP_OPTIONS_NM:
            self._z_step_combo.addItem(f"{step:.0f}", step)
        self._z_step_combo.setCurrentIndex(len(Z_STEP_OPTIONS_NM) - 1)  # 1000 nm default
        self._z_step_combo.currentIndexChanged.connect(self._update_z_range_label)
        grid.addWidget(self._z_step_combo, 0, 1)

        slices_label = QLabel("Slices:")
        slices_label.setObjectName("MotionInlineLabel")
        grid.addWidget(slices_label, 1, 0)
        self._nb_z = QSpinBox()
        self._nb_z.setRange(2, 200)
        self._nb_z.setValue(5)
        self._nb_z.valueChanged.connect(self._update_z_range_label)
        grid.addWidget(self._nb_z, 1, 1)

        outer.addLayout(grid)

        self._z_range_label = QLabel()
        self._z_range_label.setObjectName("MotionInlineLabel")
        self._z_range_label.setWordWrap(True)
        outer.addWidget(self._z_range_label)
        self._update_z_range_label()

        return cluster

    def _on_3d_toggled(self, enabled: bool) -> None:
        self._z_cluster.setVisible(enabled)
        self._z_slice_row.setVisible(enabled)
        self._max_proj.setVisible(enabled)
        self._update_z_range_label()

    def _update_z_range_label(self) -> None:
        step = self._z_step_combo.currentData()
        n = self._nb_z.value()
        if step is None:
            return
        total = step * (n - 1)
        self._z_range_label.setText(f"Range {total:.0f} nm, centred on current focus")

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
        vbox = QVBoxLayout(grp)

        hbox = QHBoxLayout()
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
        vbox.addLayout(hbox)

        # 3-D only: pick a Z slice to view, or collapse the stack with a
        # max-intensity projection. Hidden entirely in 2-D mode. A top rule
        # separates it from the 2-D harmonic/data controls above.
        self._z_slice_row = QWidget()
        self._z_slice_row.setObjectName("ZDisplaySection")
        z_col = QVBoxLayout(self._z_slice_row)
        z_col.setContentsMargins(0, 8, 0, 0)
        z_col.setSpacing(6)

        z_row = QHBoxLayout()
        z_row.setSpacing(6)
        z_slice_caption = QLabel("Z slice:")
        z_slice_caption.setObjectName("MotionInlineLabel")
        z_row.addWidget(z_slice_caption)
        self._z_slice_slider = QSlider(Qt.Orientation.Horizontal)
        self._z_slice_slider.setRange(0, 0)
        self._z_slice_slider.valueChanged.connect(self._on_z_slice_changed)
        z_row.addWidget(self._z_slice_slider, 1)
        self._z_slice_label = QLabel("0 / 0")
        self._z_slice_label.setObjectName("MotionInlineLabel")
        self._z_slice_label.setMinimumWidth(36)
        self._z_slice_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        z_row.addWidget(self._z_slice_label)
        z_col.addLayout(z_row)

        self._max_proj = QCheckBox("Max projection (collapse Z)")
        self._max_proj.toggled.connect(self._on_max_proj_toggled)
        z_col.addWidget(self._max_proj)

        vbox.addWidget(self._z_slice_row)

        return grp

    def _on_z_slice_changed(self, iz: int) -> None:
        n = self._z_slice_slider.maximum() + 1
        self._z_slice_label.setText(f"{iz} / {max(n - 1, 0)}")
        if not self._max_proj.isChecked():
            self._update_image()

    def _on_max_proj_toggled(self, checked: bool) -> None:
        self._z_slice_slider.setEnabled(not checked)
        self._update_image()

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
        is_3d = self._z_enable.isChecked()
        nb_z = self._nb_z.value() if is_3d else 1
        dz_nm = float(self._z_step_combo.currentData()) if is_3d else 0.0

        # Fresh accumulation grids (NaN until pixel received)
        self._amp_grid = np.full((nb_z, nb_y, nb_x), float("nan"))
        self._phase_grid = np.full((nb_z, nb_y, nb_x), float("nan"))

        # Clear image + Z slice picker
        self._img_item.setImage(np.zeros((nb_x, nb_y), dtype=np.float32))
        self._z_slice_slider.setRange(0, nb_z - 1)
        self._z_slice_slider.setValue(0)

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
            nb_z=nb_z,
            dz_nm=dz_nm,
        )
        self._worker.point_done.connect(self._on_point_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log_message.connect(self._log.append_line)
        self._worker.log_message.connect(self.log_message)
        self._worker.finished.connect(self._on_worker_finished)

        total = nb_x * nb_y * nb_z
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._progress_label.setText(f"0 / {total} px")

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

    def _on_point_done(
        self, ix: int, iy: int, iz: int, o_amp: object, o_phase: object
    ) -> None:
        # o_amp / o_phase are numpy arrays (shape 6,) carried as object in signal
        amp_arr: np.ndarray = o_amp  # type: ignore[assignment]
        phase_arr: np.ndarray = o_phase  # type: ignore[assignment]
        h = self._harmonic_combo.currentIndex()

        if self._amp_grid is not None:
            self._amp_grid[iz, iy, ix] = float(amp_arr[h])
        if self._phase_grid is not None:
            self._phase_grid[iz, iy, ix] = float(phase_arr[h])

        # Follow the scan through the stack so the visible slice matches
        # what's currently being acquired.
        if self._z_slice_slider.value() != iz:
            self._z_slice_slider.blockSignals(True)
            self._z_slice_slider.setValue(iz)
            self._z_slice_slider.blockSignals(False)
            self._z_slice_label.setText(f"{iz} / {self._z_slice_slider.maximum()}")

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

        volume = self._phase_grid if use_phase else self._amp_grid  # (nb_z, ny, nx)
        if volume is None:
            return

        if self._max_proj.isChecked():
            with np.errstate(all="ignore"):
                grid = np.nanmax(volume, axis=0)
        else:
            iz = self._z_slice_slider.value()
            grid = volume[iz]

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

        import contextlib
        self._z_enable.setChecked(str(s.value("z_enable", False)).lower() in ("1", "true"))
        with contextlib.suppress(Exception):
            self._nb_z.setValue(int(s.value("nb_z", 5)))
        with contextlib.suppress(Exception):
            default_idx = len(Z_STEP_OPTIONS_NM) - 1
            self._z_step_combo.setCurrentIndex(int(s.value("z_step_index", default_idx)))
        self._max_proj.setChecked(str(s.value("max_proj", False)).lower() in ("1", "true"))

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
        s.setValue("z_enable", self._z_enable.isChecked())
        s.setValue("nb_z", self._nb_z.value())
        s.setValue("z_step_index", self._z_step_combo.currentIndex())
        s.setValue("max_proj", self._max_proj.isChecked())

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(3000)
        super().closeEvent(event)  # type: ignore[misc]

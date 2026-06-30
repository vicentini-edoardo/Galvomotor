"""Main application window: connection, motion, and scan tabs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from galvo_gui.gui.panel_manual import ConnectionPanel, MotionPanel
from galvo_gui.gui.panel_scan import ScanPanel

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _GitFetchWorker(QThread):
    # (returncode, branch_list, info) — info=current_branch on success, error msg on failure
    finished = pyqtSignal(int, object, str)

    def run(self) -> None:
        try:
            r = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "fetch", "--prune"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                self.finished.emit(r.returncode, [], (r.stdout + r.stderr).strip())
                return

            rb = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "branch", "-r", "--format=%(refname:short)"],
                capture_output=True, text=True,
            )
            branches = []
            for line in rb.stdout.splitlines():
                line = line.strip()
                if not line or "->" in line:
                    continue
                prefix = "origin/"
                b = line[len(prefix):] if line.startswith(prefix) else line
                if b not in branches:
                    branches.append(b)

            cur = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "branch", "--show-current"],
                capture_output=True, text=True,
            ).stdout.strip()

            if cur in branches:
                branches.remove(cur)
                branches.insert(0, cur)

            self.finished.emit(0, branches, cur)
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(-1, [], str(exc))


class _GitSwitchWorker(QThread):
    finished = pyqtSignal(int, str)  # returncode, output/changelog

    def __init__(self, branch: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._branch = branch

    def run(self) -> None:
        try:
            head = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            ).stdout.strip()

            r = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "checkout", self._branch],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                self.finished.emit(r.returncode, (r.stdout + r.stderr).strip())
                return

            r2 = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "pull"],
                capture_output=True, text=True, timeout=30,
            )
            if r2.returncode != 0:
                self.finished.emit(r2.returncode, (r2.stdout + r2.stderr).strip())
                return

            log = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "log", f"{head}..HEAD",
                 "--pretty=format:• %s", "--no-merges"],
                capture_output=True, text=True,
            ).stdout.strip()

            self.finished.emit(0, log if log else "Already up to date.")
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(-1, str(exc))


class _BranchDialog(QDialog):
    def __init__(self, branches: list, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Branch")
        self.setMinimumWidth(300)

        self._combo = QComboBox()
        for b in branches:
            self._combo.addItem(b)
        idx = self._combo.findText(current)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)

        btn_ok = QPushButton("Update")
        btn_ok.setProperty("accent", True)
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select branch to switch to and pull:"))
        layout.addWidget(self._combo)
        layout.addLayout(btn_row)

    def selected_branch(self) -> str:
        return self._combo.currentText()


class MainWindow(QMainWindow):
    """Top-level window: connection, motion, and scan tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Galvo Motor Control")

        self._tabs = QTabWidget(self)
        self.setCentralWidget(self._tabs)

        self._connection = ConnectionPanel(self)
        self._motion = MotionPanel(self)
        self._scan = ScanPanel(self)
        self._tabs.addTab(self._connection, "Connection")
        self._tabs.addTab(self._motion, "Motion")
        self._tabs.addTab(self._scan, "Scan")

        # Wire backend connect/disconnect → motion and scan panels
        self._connection.backend_connected.connect(self._motion.set_backend)
        self._connection.backend_connected.connect(self._scan.set_backend)
        self._connection.backend_disconnected.connect(self._motion.clear_backend)
        self._connection.backend_disconnected.connect(self._scan.clear_backend)

        # Lock jog controls during scan
        self._scan.running_changed.connect(self._motion.lock_for_scan)

        # Status bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        self._conn_label = QLabel("● Not connected")
        self._conn_label.setObjectName("connection_label")
        self._conn_label.setProperty("connected", "false")
        self._status_bar.addWidget(self._conn_label)

        self._scan_label = QLabel("Scan: Idle")
        self._scan_label.setObjectName("acquisition_label")
        self._scan_label.setProperty("running", "false")
        self._status_bar.addWidget(self._scan_label)

        self._fetch_worker: _GitFetchWorker | None = None
        self._switch_worker: _GitSwitchWorker | None = None

        if (_REPO_ROOT / ".git").exists():
            self._update_btn: QPushButton | None = QPushButton("Update")
            self._update_btn.setToolTip("Switch branch and pull")
            self._update_btn.clicked.connect(self._update_app)
            self._status_bar.addPermanentWidget(self._update_btn)
        else:
            self._update_btn = None

        self._status_bar.addPermanentWidget(QLabel("galvo-gui v0.1"), 0)

        # Wire status updates
        self._connection.backend_connected.connect(self._on_connected)
        self._connection.backend_disconnected.connect(self._on_disconnected)
        self._scan.running_changed.connect(self._on_scan_running)
        self._connection.log_message.connect(self._status_bar.showMessage)
        self._motion.log_message.connect(self._status_bar.showMessage)
        self._scan.log_message.connect(self._status_bar.showMessage)

    # ------------------------------------------------------------------

    def _on_connected(self, _backend: object) -> None:
        self._conn_label.setText("● Connected")
        self._conn_label.setProperty("connected", "true")
        self._conn_label.style().unpolish(self._conn_label)  # type: ignore[union-attr]
        self._conn_label.style().polish(self._conn_label)    # type: ignore[union-attr]

    def _on_disconnected(self) -> None:
        self._conn_label.setText("● Not connected")
        self._conn_label.setProperty("connected", "false")
        self._conn_label.style().unpolish(self._conn_label)  # type: ignore[union-attr]
        self._conn_label.style().polish(self._conn_label)    # type: ignore[union-attr]

    def _on_scan_running(self, running: bool) -> None:
        self._scan_label.setText("Scan: Running" if running else "Scan: Idle")
        self._scan_label.setProperty("running", "true" if running else "false")
        self._scan_label.style().unpolish(self._scan_label)  # type: ignore[union-attr]
        self._scan_label.style().polish(self._scan_label)    # type: ignore[union-attr]
        # Switch to Scan tab automatically when scan starts
        if running:
            self._tabs.setCurrentWidget(self._scan)

    # ------------------------------------------------------------------
    # Update (git fetch → branch picker → checkout + pull)
    # ------------------------------------------------------------------

    def _update_app(self) -> None:
        if self._update_btn is None:
            return
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Fetching…")
        self._fetch_worker = _GitFetchWorker()
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_fetch_done(self, returncode: int, branches: object, info: str) -> None:
        if self._update_btn is not None:
            self._update_btn.setEnabled(True)
            self._update_btn.setText("Update")
        if returncode != 0:
            QMessageBox.warning(self, "Update", f"Fetch failed:\n{info}")
            return
        branch_list: list = branches  # type: ignore[assignment]
        if not branch_list:
            QMessageBox.information(self, "Update", "No remote branches found.")
            return
        dlg = _BranchDialog(branch_list, info, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dlg.selected_branch()
        if self._update_btn is not None:
            self._update_btn.setEnabled(False)
            self._update_btn.setText("Updating…")
        self._switch_worker = _GitSwitchWorker(selected, self)
        self._switch_worker.finished.connect(self._on_switch_done)
        self._switch_worker.start()

    def _on_switch_done(self, returncode: int, output: str) -> None:
        if self._update_btn is not None:
            self._update_btn.setEnabled(True)
            self._update_btn.setText("Update")
        msg = QMessageBox(self)
        msg.setWindowTitle("Update")
        if returncode != 0:
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText("Update failed.")
            msg.setInformativeText(output)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
        elif output == "Already up to date.":
            msg.setText("Already up to date.")
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
        else:
            msg.setText("What's new:")
            msg.setInformativeText(output)
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            msg.button(QMessageBox.StandardButton.Yes).setText("Restart now")
            msg.button(QMessageBox.StandardButton.No).setText("Later")
            if msg.exec() == QMessageBox.StandardButton.Yes:
                self._restart()

    def _restart(self) -> None:
        subprocess.Popen([sys.executable, "-m", "galvo_gui"])
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ------------------------------------------------------------------

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        # Propagate close to panels so they save QSettings and stop workers
        self._scan.closeEvent(event)
        self._motion.closeEvent(event)
        self._connection.closeEvent(event)
        super().closeEvent(event)  # type: ignore[misc]

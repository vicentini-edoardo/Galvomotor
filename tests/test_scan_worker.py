"""Integration test: ScanWorker end-to-end with MockGalvoBackend."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

import h5py
from PyQt6.QtWidgets import QApplication

from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend
from galvo_gui.workers.scan import ScanWorker


@pytest.fixture(scope="module")
def qapp():  # type: ignore[no-untyped-def]
    app = QApplication.instance() or QApplication([])
    return app


def test_scan_worker_end_to_end(tmp_path: Any, qapp: Any, qtbot: Any) -> None:  # noqa: F821
    """Worker completes a 3×3 scan and saves a valid HDF5."""
    galvo = MockGalvoBackend()
    nea = MockNeaBackend(seed=7)
    galvo.connect()
    nea.connect()

    worker = ScanWorker(
        galvo=galvo,
        nea=nea,
        dx_nm=300.0,
        dy_nm=300.0,
        nb_x=3,
        nb_y=3,
        twait=0.0,
        t_integ_s=0.001,
        save_dir=str(tmp_path),
        filename="test_scan",
    )

    finished_paths: list = []
    worker.scan_finished.connect(lambda p: finished_paths.append(p))

    errors: list = []
    worker.error.connect(lambda e: errors.append(e))

    with qtbot.waitSignal(worker.finished, timeout=30_000):
        worker.start()

    assert not errors, f"Worker errors: {errors}"
    assert len(finished_paths) == 1

    # Verify HDF5 contents
    with h5py.File(finished_paths[0], "r") as h5:
        for h in range(6):
            assert f"O{h}" in h5
            assert h5[f"O{h}"].shape == (3, 3)
        assert "coordinates" in h5
        assert h5["coordinates"].shape == (3, 3, 2)

    galvo.disconnect()
    nea.disconnect()


def test_scan_worker_stop(tmp_path: Any, qapp: Any, qtbot: Any) -> None:  # noqa: F821
    """Worker stops cooperatively when stop() is called."""
    galvo = MockGalvoBackend()
    nea = MockNeaBackend(seed=0)
    galvo.connect()
    nea.connect()

    worker = ScanWorker(
        galvo=galvo,
        nea=nea,
        dx_nm=500.0,
        dy_nm=500.0,
        nb_x=10,
        nb_y=10,
        twait=0.0,
        t_integ_s=0.001,
        save_dir=str(tmp_path),
        filename="test_stop",
    )

    # Stop after first progress signal
    def stop_it(done: int, total: int) -> None:
        if done >= 1:
            worker.stop()

    worker.progress.connect(stop_it)

    with qtbot.waitSignal(worker.finished, timeout=30_000):
        worker.start()

    # Worker finished without crashing
    galvo.disconnect()
    nea.disconnect()

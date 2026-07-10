"""Integration test: ScanWorker end-to-end with MockGalvoBackend."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

import h5py
import json
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
        dx_pulses=300.0,
        dy_pulses=300.0,
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
    text_path = finished_paths[0].replace(".h5", ".txt")

    # Verify HDF5 contents
    with h5py.File(finished_paths[0], "r") as h5:
        for h in range(6):
            assert f"O{h}" in h5
            assert h5[f"O{h}"].shape == (3, 3)
        assert "coordinates" in h5
        assert h5["coordinates"].shape == (3, 3, 2)
        metadata = json.loads(h5.attrs["metadata"])
        assert metadata["dx_pulses"] == 300.0
        assert metadata["dy_pulses"] == 300.0

    text_lines = open(text_path, encoding="utf-8").read().splitlines()
    assert text_lines[0] == "# scan_mode: 2D"
    assert text_lines[1] == "# dx_pulses: 300.0"
    assert text_lines[2] == "# dy_pulses: 300.0"
    assert any(line == "# position_conversion_factor_parameters:" for line in text_lines)
    assert any(line == "#   pulses_per_nm: 1.0" for line in text_lines)
    assert any(line == "#   x_nm_expression: x_nm = x_pulse / 1" for line in text_lines)
    assert any(
        line.startswith("# #row - #col - x_pulse - y_pulse - O0A - O0P")
        for line in text_lines
    )

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
        dx_pulses=500.0,
        dy_pulses=500.0,
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


def test_scan_worker_3d_end_to_end(tmp_path: Any, qapp: Any, qtbot: Any) -> None:  # noqa: F821
    """3-D scan (nb_z>1): visits every (ix, iy, iz), Z returns to start, saves a (nz,ny,nx) HDF5."""
    galvo = MockGalvoBackend()
    nea = MockNeaBackend(seed=3)
    galvo.connect()
    nea.connect()
    z_before = nea.read_z_nm()

    worker = ScanWorker(
        galvo=galvo,
        nea=nea,
        dx_pulses=200.0,
        dy_pulses=200.0,
        nb_x=3,
        nb_y=3,
        twait=0.0,
        t_integ_s=0.001,
        save_dir=str(tmp_path),
        filename="test_scan_3d",
        nb_z=3,
        dz_nm=500.0,
    )

    points_seen: set = set()
    worker.point_done.connect(lambda ix, iy, iz, a, p: points_seen.add((ix, iy, iz)))

    finished_paths: list = []
    worker.scan_finished.connect(lambda p: finished_paths.append(p))
    errors: list = []
    worker.error.connect(lambda e: errors.append(e))

    with qtbot.waitSignal(worker.finished, timeout=30_000):
        worker.start()

    assert not errors, f"Worker errors: {errors}"
    assert len(finished_paths) == 1
    assert points_seen == {(ix, iy, iz) for ix in range(3) for iy in range(3) for iz in range(3)}
    # Z stack is centred on the starting focus, so the net motion is zero.
    assert nea.read_z_nm() == pytest.approx(z_before)

    with h5py.File(finished_paths[0], "r") as h5:
        for h in range(6):
            assert h5[f"O{h}"].shape == (3, 3, 3)
        assert h5["coordinates"].shape == (3, 3, 3, 2)
        assert h5["coordinates_z"].shape == (3,)
        metadata = json.loads(h5.attrs["metadata"])
        assert metadata["scan_mode"] == "3D"
        assert metadata["nb_z"] == 3
        assert metadata["dz_nm"] == 500.0

    galvo.disconnect()
    nea.disconnect()


def test_scan_worker_2d_save_shape_unchanged(tmp_path: Any, qapp: Any, qtbot: Any) -> None:  # noqa: F821
    """Regression: nb_z=1 (default) still writes plain (ny, nx) datasets, no z dataset."""
    galvo = MockGalvoBackend()
    nea = MockNeaBackend(seed=1)
    galvo.connect()
    nea.connect()

    worker = ScanWorker(
        galvo=galvo,
        nea=nea,
        dx_pulses=200.0,
        dy_pulses=200.0,
        nb_x=3,
        nb_y=3,
        twait=0.0,
        t_integ_s=0.001,
        save_dir=str(tmp_path),
        filename="test_scan_2d",
    )

    finished_paths: list = []
    worker.scan_finished.connect(lambda p: finished_paths.append(p))

    with qtbot.waitSignal(worker.finished, timeout=30_000):
        worker.start()

    with h5py.File(finished_paths[0], "r") as h5:
        assert h5["O0"].shape == (3, 3)
        assert h5["coordinates"].shape == (3, 3, 2)
        assert "coordinates_z" not in h5
        metadata = json.loads(h5.attrs["metadata"])
        assert metadata["scan_mode"] == "2D"
        assert "nb_z" not in metadata

    galvo.disconnect()
    nea.disconnect()

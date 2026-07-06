"""Tests for io/save.py HDF5 write and round-trip."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from galvo_gui.io.save import save_scan_h5


def _make_arrays(ny: int = 4, nx: int = 5) -> tuple:
    rng = np.random.default_rng(0)
    amp = rng.random((6, ny, nx))
    phase = rng.random((6, ny, nx)) * 2 * np.pi - np.pi
    coords = rng.random((ny, nx, 2)) * 1000.0
    return amp, phase, coords


def test_roundtrip_shapes(tmp_path: Any) -> None:  # noqa: F821
    ny, nx = 4, 5
    amp, phase, coords = _make_arrays(ny, nx)
    coords_pulses = coords * 0.1108
    meta = {"test": True, "nx": nx, "ny": ny}
    path = tmp_path / "scan.h5"

    save_scan_h5(path, amp, phase, coords, coords_pulses, meta)

    assert path.exists()
    with h5py.File(path, "r") as h5:
        for h in range(6):
            assert f"O{h}" in h5
            assert h5[f"O{h}"].shape == (ny, nx)
            assert h5[f"amp_O{h}"].shape == (ny, nx)
            assert h5[f"phase_O{h}"].shape == (ny, nx)
        assert "coordinates" in h5
        assert h5["coordinates"].shape == (ny, nx, 2)
        assert "coordinates_pulses" in h5
        assert h5["coordinates_pulses"].shape == (ny, nx, 2)


def test_complex_values_correct(tmp_path: Any) -> None:  # noqa: F821
    """O0 = amp[0] * exp(1j * phase[0])."""
    amp, phase, coords = _make_arrays()
    path = tmp_path / "scan.h5"
    save_scan_h5(path, amp, phase, coords, coords, {})

    with h5py.File(path, "r") as h5:
        o0 = h5["O0"][:]
        expected = amp[0] * np.exp(1j * phase[0])
        np.testing.assert_allclose(np.abs(o0), np.abs(expected), rtol=1e-5)


def test_metadata_stored(tmp_path: Any) -> None:  # noqa: F821
    import json
    amp, phase, coords = _make_arrays()
    meta = {"dx_nm": 500.0, "nb_x": 5}
    path = tmp_path / "scan.h5"
    save_scan_h5(path, amp, phase, coords, coords, meta)

    with h5py.File(path, "r") as h5:
        recovered = json.loads(h5.attrs["metadata"])
    assert recovered["dx_nm"] == 500.0
    assert recovered["nb_x"] == 5


def test_atomic_on_error(tmp_path: Any) -> None:  # noqa: F821
    """No partial file left if save raises."""
    path = tmp_path / "broken.h5"

    # Pass a non-array to force a write error (AttributeError from .astype on str)
    with pytest.raises((TypeError, AttributeError)):
        save_scan_h5(path, "bad", "bad", "bad", "bad", {})  # type: ignore[arg-type]

    assert not path.exists()
    assert not (path.parent / (path.name + ".tmp")).exists()

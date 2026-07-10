"""Tests for io/save.py save helpers and round-trip."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from galvo_gui.io.save import save_scan_h5, save_scan_text


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
    meta = {"dx_pulses": 500.0, "nb_x": 5}
    path = tmp_path / "scan.h5"
    save_scan_h5(path, amp, phase, coords, coords, meta)

    with h5py.File(path, "r") as h5:
        recovered = json.loads(h5.attrs["metadata"])
    assert recovered["dx_pulses"] == 500.0
    assert recovered["nb_x"] == 5


def test_atomic_on_error(tmp_path: Any) -> None:  # noqa: F821
    """No partial file left if save raises."""
    path = tmp_path / "broken.h5"

    # Pass a non-array to force a write error (AttributeError from .astype on str)
    with pytest.raises((TypeError, AttributeError)):
        save_scan_h5(path, "bad", "bad", "bad", "bad", {})  # type: ignore[arg-type]

    assert not path.exists()
    assert not (path.parent / (path.name + ".tmp")).exists()


def test_text_export_contains_metadata_and_rows(tmp_path: Any) -> None:  # noqa: F821
    amp, phase, coords = _make_arrays(2, 3)
    coords_pulses = coords * 0.1108
    meta = {
        "dx_pulses": 500.0,
        "nb_x": 3,
        "position_conversion_factor_parameters": {
            "pulses_per_nm": 1.0,
            "nm_per_pulse": 1.0,
            "x_nm_expression": "x_nm = x_pulse / 1",
            "y_nm_expression": "y_nm = y_pulse / 1",
        },
    }
    path = tmp_path / "scan.txt"

    save_scan_text(path, amp, phase, coords_pulses, meta)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# dx_pulses: 500.0"
    assert lines[1] == "# nb_x: 3"
    assert lines[2] == "# position_conversion_factor_parameters:"
    assert lines[3] == "#   pulses_per_nm: 1.0"
    assert lines[4] == "#   nm_per_pulse: 1.0"
    assert lines[5] == "#   x_nm_expression: x_nm = x_pulse / 1"
    assert lines[6] == "#   y_nm_expression: y_nm = y_pulse / 1"
    assert lines[7].startswith("# #row - #col - x_pulse - y_pulse - O0A - O0P")
    assert len(lines) == 8 + (2 * 3)

    fields = lines[8].split(" - ")
    assert fields[0] == "0"
    assert fields[1] == "0"
    assert len(fields) == 4 + (6 * 2)

    second_row_fields = lines[11].split(" - ")
    assert second_row_fields[0] == "1"
    assert second_row_fields[1] == "0"


def _make_arrays_3d(nz: int = 2, ny: int = 3, nx: int = 4) -> tuple:
    rng = np.random.default_rng(1)
    amp = rng.random((6, nz, ny, nx))
    phase = rng.random((6, nz, ny, nx)) * 2 * np.pi - np.pi
    coords = rng.random((nz, ny, nx, 2)) * 1000.0
    coords_z = np.array([iz * 500.0 for iz in range(nz)])
    return amp, phase, coords, coords_z


def test_roundtrip_shapes_3d(tmp_path: Any) -> None:  # noqa: F821
    nz, ny, nx = 2, 3, 4
    amp, phase, coords, coords_z = _make_arrays_3d(nz, ny, nx)
    coords_pulses = coords * 0.1108
    path = tmp_path / "scan3d.h5"

    save_scan_h5(path, amp, phase, coords, coords_pulses, {"nb_z": nz}, coords_z=coords_z)

    with h5py.File(path, "r") as h5:
        for h in range(6):
            assert h5[f"O{h}"].shape == (nz, ny, nx)
        assert h5["coordinates"].shape == (nz, ny, nx, 2)
        assert h5["coordinates_z"].shape == (nz,)
        np.testing.assert_allclose(h5["coordinates_z"][:], coords_z)


def test_text_export_3d_has_slice_column(tmp_path: Any) -> None:  # noqa: F821
    nz, ny, nx = 2, 2, 2
    amp, phase, coords, coords_z = _make_arrays_3d(nz, ny, nx)
    coords_pulses = coords * 0.1108
    path = tmp_path / "scan3d.txt"

    save_scan_text(path, amp, phase, coords_pulses, {"nb_z": nz}, coords_z=coords_z)

    lines = path.read_text(encoding="utf-8").splitlines()
    header_line = next(line for line in lines if line.startswith("# #slice"))
    assert header_line.startswith("# #slice - z_nm - #row - #col - x_pulse - y_pulse - O0A - O0P")

    data_lines = [line for line in lines if not line.startswith("#")]
    assert len(data_lines) == nz * ny * nx
    first = data_lines[0].split(" - ")
    assert first[0] == "0"  # slice index
    assert float(first[1]) == pytest.approx(coords_z[0])

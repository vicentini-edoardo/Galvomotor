"""Tests for run_raster_scan geometry over the mock backends."""

from __future__ import annotations

import pytest

from galvo_gui.motion.base import run_raster_scan
from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend


def _make_backends():
    galvo = MockGalvoBackend()
    nea = MockNeaBackend()
    galvo.connect()
    nea.connect()
    return galvo, nea


def test_scan_visits_all_pixels() -> None:
    """run_raster_scan calls on_point exactly nb_x * nb_y times."""
    galvo, nea = _make_backends()

    visited: list = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        visited.append((ix, iy))

    run_raster_scan(galvo, nea, 500.0, 500.0, 5, 4, 0.0, 0.001,
                    on_point, lambda: False, settle=lambda _t: None)
    assert len(visited) == 20
    # All (ix, iy) pairs present exactly once
    assert set(visited) == {(ix, iy) for iy in range(4) for ix in range(5)}


def test_scan_stop_flag() -> None:
    """stop_check=True stops the scan early."""
    galvo, nea = _make_backends()

    visited: list = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        visited.append((ix, iy))

    run_raster_scan(galvo, nea, 200.0, 200.0, 10, 10, 0.0, 0.001,
                    on_point, lambda: True, settle=lambda _t: None)
    assert len(visited) < 10 * 10


def test_scan_returns_to_center() -> None:
    """After a scan the galvo position should be back near (0, 0)."""
    galvo, nea = _make_backends()

    run_raster_scan(galvo, nea, 400.0, 400.0, 4, 4, 0.0, 0.001,
                    lambda ix, iy, s: None, lambda: False, settle=lambda _t: None)
    x, y = galvo.read_xy_nm()
    assert abs(x) < 1e-6
    assert abs(y) < 1e-6


def test_scan_hits_requested_edges() -> None:
    """Scan coordinates should span the full requested range, inclusive."""
    galvo, nea = _make_backends()

    coords: list[tuple[float, float]] = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        coords.append(sample.xy_nm)

    run_raster_scan(galvo, nea, 400.0, 200.0, 5, 3, 0.0, 0.001,
                    on_point, lambda: False, settle=lambda _t: None)

    xs = sorted({round(x, 6) for x, _ in coords})
    ys = sorted({round(y, 6) for _, y in coords})
    assert xs == [-200.0, -100.0, 0.0, 100.0, 200.0]
    assert ys == [-100.0, 0.0, 100.0]


def test_scan_sample_decay_with_distance() -> None:
    """O2 amplitude at centre > O2 at far corner (gaussian spot)."""
    nea = MockNeaBackend(seed=0)
    nea.connect()

    center_amp = nea.read_sample(0.001, (0.0, 0.0)).o_amp[2]
    far_amp = nea.read_sample(0.001, (3000.0, 3000.0)).o_amp[2]

    assert center_amp > far_amp


def test_disconnected_raises() -> None:
    from galvo_gui.motion.base import GalvoError
    backend = MockGalvoBackend()
    with pytest.raises(GalvoError):
        backend.read_xy_nm()

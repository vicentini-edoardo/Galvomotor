"""Tests for MockGalvoBackend scan geometry."""

from __future__ import annotations

import pytest

from galvo_gui.motion.mock import MockGalvoBackend


def test_scan_visits_all_pixels() -> None:
    """scan() calls on_point exactly nb_x * nb_y times."""
    backend = MockGalvoBackend()
    backend.connect()

    visited: list = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        visited.append((ix, iy))

    backend.scan(500.0, 500.0, 5, 4, 0.0, 0.001, on_point, lambda: False)
    assert len(visited) == 20
    # All (ix, iy) pairs present exactly once
    assert set(visited) == {(ix, iy) for iy in range(4) for ix in range(5)}


def test_scan_stop_flag() -> None:
    """stop_check=True stops the scan early."""
    backend = MockGalvoBackend()
    backend.connect()

    visited: list = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        visited.append((ix, iy))

    backend.scan(200.0, 200.0, 10, 10, 0.0, 0.001, on_point, lambda: True)
    # With stop_check=True from the start, the first pixel may be read (the
    # stop check runs before each pixel in the outer/inner loops), so at most
    # a handful of pixels are visited.
    assert len(visited) < 10 * 10


def test_scan_returns_to_center() -> None:
    """After a scan the backend position should be back near (0, 0)."""
    backend = MockGalvoBackend()
    backend.connect()

    backend.scan(400.0, 400.0, 4, 4, 0.0, 0.001, lambda ix, iy, s: None, lambda: False)
    x, y = backend.read_xy_nm()
    assert abs(x) < 1e-6
    assert abs(y) < 1e-6


def test_scan_hits_requested_edges() -> None:
    """Scan coordinates should span the full requested range, inclusive."""
    backend = MockGalvoBackend()
    backend.connect()

    coords: list[tuple[float, float]] = []

    def on_point(ix: int, iy: int, sample: object) -> None:
        coords.append(sample.xy_nm)

    backend.scan(400.0, 200.0, 5, 3, 0.0, 0.001, on_point, lambda: False)

    xs = sorted({round(x, 6) for x, _ in coords})
    ys = sorted({round(y, 6) for _, y in coords})
    assert xs == [-200.0, -100.0, 0.0, 100.0, 200.0]
    assert ys == [-100.0, 0.0, 100.0]


def test_scan_sample_decay_with_distance() -> None:
    """O2 amplitude at centre > O2 at far corner (gaussian spot)."""
    backend = MockGalvoBackend(seed=0)
    backend.connect()

    center_amp = backend.read_sample(0.001).o_amp[2]
    backend.move_relative(3000.0, 3000.0)
    far_amp = backend.read_sample(0.001).o_amp[2]
    backend.goto_center()

    assert center_amp > far_amp


def test_disconnected_raises() -> None:
    from galvo_gui.motion.base import GalvoError
    backend = MockGalvoBackend()
    with pytest.raises(GalvoError):
        backend.read_xy_nm()

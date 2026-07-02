"""Tests for MockGalvoBackend motion and signal primitives."""

from __future__ import annotations

import pytest

from galvo_gui.motion.base import GalvoError, SnomSample
from galvo_gui.motion.mock import MockGalvoBackend


def test_connect_disconnect() -> None:
    b = MockGalvoBackend()
    assert not b.is_connected()
    b.connect()
    assert b.is_connected()
    b.disconnect()
    assert not b.is_connected()


def test_move_relative_accumulates() -> None:
    b = MockGalvoBackend()
    b.connect()
    b.move_relative(100.0, 200.0)
    b.move_relative(-50.0, 0.0)
    x, y = b.read_xy_nm()
    assert abs(x - 50.0) < 1e-9
    assert abs(y - 200.0) < 1e-9


def test_goto_center_resets() -> None:
    b = MockGalvoBackend()
    b.connect()
    b.move_relative(1000.0, -500.0)
    b.move_z_relative(250.0)
    b.goto_center()
    x, y = b.read_xy_nm()
    z = b.read_z_nm()
    assert abs(x) < 1e-9
    assert abs(y) < 1e-9
    assert abs(z) < 1e-9


def test_read_sample_shape() -> None:
    b = MockGalvoBackend()
    b.connect()
    s = b.read_sample(0.001)
    assert isinstance(s, SnomSample)
    assert s.o_amp.shape == (6,)
    assert s.o_phase.shape == (6,)


def test_signal_decays_with_distance() -> None:
    """O0 amplitude is highest at origin and decreases away."""
    b = MockGalvoBackend(seed=99)
    b.connect()
    amp_center = b.read_sample(0.001).o_amp[0]
    b.move_relative(5000.0, 5000.0)
    amp_far = b.read_sample(0.001).o_amp[0]
    assert amp_center > amp_far


def test_not_connected_raises() -> None:
    b = MockGalvoBackend()
    with pytest.raises(GalvoError):
        b.move_relative(1.0, 0.0)
    with pytest.raises(GalvoError):
        b.move_z_relative(1.0)
    with pytest.raises(GalvoError):
        b.read_xy_nm()
    with pytest.raises(GalvoError):
        b.read_z_nm()
    with pytest.raises(GalvoError):
        b.goto_center()
    with pytest.raises(GalvoError):
        b.read_sample()


def test_z_motion_and_available_steps() -> None:
    b = MockGalvoBackend()
    b.connect()

    assert b.available_xy_steps_nm() == (0.1, 1.0, 10.0, 100.0)
    assert b.available_z_steps_nm() == (10.0, 100.0, 1000.0, 10000.0)

    b.move_z_relative(10.0)

    assert abs(b.read_z_nm() - 10.0) < 1e-9

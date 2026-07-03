"""Tests for the mock galvo (XY) and neaSNOM (Z + optical) backends."""

from __future__ import annotations

import pytest

from galvo_gui.motion.base import GalvoError, SnomSample
from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend

# ----------------------------------------------------------------------
# Galvo (XY)
# ----------------------------------------------------------------------


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
    b.goto_center()
    x, y = b.read_xy_nm()
    assert abs(x) < 1e-9
    assert abs(y) < 1e-9


def test_set_home_redefines_origin_and_goto_center() -> None:
    b = MockGalvoBackend()
    b.connect()
    b.move_relative(1000.0, -500.0)

    assert b.set_home() == (1000.0, -500.0)
    assert b.read_xy_nm() == (0.0, 0.0)

    b.move_relative(25.0, -75.0)
    assert b.read_xy_nm() == (25.0, -75.0)

    b.goto_center()
    assert b.read_xy_nm() == (0.0, 0.0)


def test_available_xy_steps() -> None:
    b = MockGalvoBackend()
    b.connect()
    assert b.available_xy_steps_pulses() == (1.0, 10.0, 100.0, 1000.0, 10000.0)


def test_move_relative_pulses_accumulates() -> None:
    b = MockGalvoBackend()
    b.connect()
    b.move_relative_pulses(100.0, 200.0)
    b.move_relative_pulses(-50.0, 0.0)
    assert b.read_xy_pulses() == (50.0, 200.0)


def test_galvo_not_connected_raises() -> None:
    b = MockGalvoBackend()
    with pytest.raises(GalvoError):
        b.move_relative(1.0, 0.0)
    with pytest.raises(GalvoError):
        b.read_xy_nm()
    with pytest.raises(GalvoError):
        b.goto_center()


# ----------------------------------------------------------------------
# neaSNOM (Z + optical)
# ----------------------------------------------------------------------


def test_read_sample_shape() -> None:
    b = MockNeaBackend()
    b.connect()
    s = b.read_sample(0.001)
    assert isinstance(s, SnomSample)
    assert s.o_amp.shape == (6,)
    assert s.o_phase.shape == (6,)


def test_signal_decays_with_distance() -> None:
    """O0 amplitude is highest at origin and decreases away."""
    b = MockNeaBackend(seed=99)
    b.connect()
    amp_center = b.read_sample(0.001, (0.0, 0.0)).o_amp[0]
    amp_far = b.read_sample(0.001, (5000.0, 5000.0)).o_amp[0]
    assert amp_center > amp_far


def test_sample_is_tagged_with_supplied_position() -> None:
    b = MockNeaBackend()
    b.connect()
    s = b.read_sample(0.001, (123.0, -45.0))
    assert s.xy_nm == (123.0, -45.0)


def test_z_motion_and_available_steps() -> None:
    b = MockNeaBackend()
    b.connect()

    assert b.available_z_steps_nm() == (10.0, 100.0, 1000.0, 10000.0)

    b.move_z_relative(10.0)
    assert abs(b.read_z_nm() - 10.0) < 1e-9


def test_nea_not_connected_raises() -> None:
    b = MockNeaBackend()
    with pytest.raises(GalvoError):
        b.move_z_relative(1.0)
    with pytest.raises(GalvoError):
        b.read_z_nm()
    with pytest.raises(GalvoError):
        b.read_sample()

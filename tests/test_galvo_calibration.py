from __future__ import annotations

import threading

from galvo_gui.motion import galvo_nea


def test_average_axis_offset_ignores_noise_and_averages_remaining_samples() -> None:
    samples = [12.0, 29.0, 31.0, -18.0, 41.0]

    offset = galvo_nea._average_axis_offset(samples, noise_threshold_p=20.0)

    assert offset == 34.0


def test_average_axis_offset_returns_zero_when_all_samples_are_noise() -> None:
    offset = galvo_nea._average_axis_offset([5.0, -20.0, 19.0], noise_threshold_p=20.0)

    assert offset == 0.0


def test_real_backend_applies_offset_to_relative_and_absolute_targets(monkeypatch) -> None:
    monkeypatch.setattr(galvo_nea, "GALVO_AVAILABLE", True)
    backend = galvo_nea.RealGalvoBackend()
    backend._connected = True
    backend._x_goto_bias = 0
    backend._offset_x_p = 30.0
    backend._offset_y_p = 0.0
    backend._offset_enabled = True

    commands: list[tuple[int, int]] = []

    monkeypatch.setattr(backend, "_read_gb511_bits", lambda: (1000, 2000))
    monkeypatch.setattr(backend, "_wait_for_axis_follow", lambda *args: (1100, 2000))
    monkeypatch.setattr(backend, "_command_goto", lambda gx, gy: commands.append((gx, gy)))

    backend.move_relative_pulses(100.0, 0.0)
    backend._goto_read_units(1100.0, 2000.0, ref=(1000, 2000))

    expected = round(galvo_nea._GOTO_PER_READ_X * 1070.0)
    assert commands[0][0] == expected
    assert commands[1][0] == expected


def test_mock_backend_calibration_is_zero_and_toggleable() -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    backend = MockGalvoBackend()
    backend.connect()

    backend.set_offset_correction_enabled(False)

    assert backend.offset_correction_enabled() is False
    assert backend.run_offset_calibration() == (0.0, 0.0)

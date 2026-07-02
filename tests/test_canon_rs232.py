from __future__ import annotations

import pytest

from galvo_gui.motion.base import GalvoError
from galvo_gui.motion.canon.rs232 import CanonErrors, CanonRS232, CanonStatus


class FakeSerial:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.is_open = True

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def readline(self) -> bytes:
        if not self.replies:
            return b""
        return self.replies.pop(0)

    def close(self) -> None:
        self.is_open = False


def test_servo_on_writes_expected_ascii_frame() -> None:
    fake = FakeSerial([b"A1C004/0\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM7")
    rs.connect()

    rs.servo_on(1)

    assert fake.writes == [b"A1C004/1\n"]


def test_status_read_without_argument_still_sends_required_delimiter() -> None:
    fake = FakeSerial([b"A1C014/3\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM7")
    rs.connect()

    rs.read_status(1)

    assert fake.writes == [b"A1C014/\n"]


def test_read_status_decodes_named_bits() -> None:
    fake = FakeSerial([b"A2C014/5495\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM9")
    rs.connect()

    status = rs.read_status(2)

    assert isinstance(status, CanonStatus)
    assert status.servo_on is True
    assert status.sync is True
    assert status.origining is True
    assert status.program_coordinates is True
    assert status.z_phase_warning is True
    assert status.moving is True
    assert status.encoder_warning is True
    assert status.target_relative is True
    assert status.target_position_set is False
    assert status.linearity_calibrating is False


def test_read_errors_decodes_named_bits() -> None:
    fake = FakeSerial([b"A1C015/21752\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM5")
    rs.connect()

    errors = rs.read_errors(1)

    assert isinstance(errors, CanonErrors)
    assert errors.clock_lack is True
    assert errors.driver_overheat is True
    assert errors.motor_overheat is True
    assert errors.format_error is True
    assert errors.command_data_error is True
    assert errors.parameter_error is False
    assert errors.status_error is False
    assert errors.communication_error is True
    assert errors.origin_detection_error is False
    assert errors.encoder_signal_error is True
    assert errors.servo_off_by_hardware is True
    assert errors.current_saturation is False


def test_reply_axis_mismatch_raises() -> None:
    fake = FakeSerial([b"A2C004/0\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM1")
    rs.connect()

    with pytest.raises(GalvoError, match="Unexpected reply"):
        rs.servo_on(1)


def test_connect_uses_manual_port_before_auto_detect() -> None:
    captured = {}

    def serial_factory(**kwargs):
        captured.update(kwargs)
        return FakeSerial([])

    rs = CanonRS232(serial_factory=serial_factory, port_selector=lambda: "COM99")
    rs.connect(port="COM3")

    assert captured["port"] == "COM3"
    assert captured["baudrate"] == 38400


def test_read_temperature_divides_only_degree_c_sources() -> None:
    fake = FakeSerial([b"A1C011/12345\n", b"A1C011/2345\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM3")
    rs.connect()

    adc_value = rs.read_temperature(1, 0)
    temp_c = rs.read_temperature(1, 10)

    assert adc_value == 12345.0
    assert temp_c == 23.45

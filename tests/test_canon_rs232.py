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


def test_read_status_decodes_named_bits() -> None:
    fake = FakeSerial([b"A2C014/19\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM9")
    rs.connect()

    status = rs.read_status(2)

    assert isinstance(status, CanonStatus)
    assert status.servo_on is True
    assert status.sync is True
    assert status.origining is True


def test_read_errors_decodes_named_bits() -> None:
    fake = FakeSerial([b"A1C015/72\n"])
    rs = CanonRS232(serial_factory=lambda **_: fake, port_selector=lambda: "COM5")
    rs.connect()

    errors = rs.read_errors(1)

    assert isinstance(errors, CanonErrors)
    assert errors.clock_lack is True
    assert errors.format_error is True


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

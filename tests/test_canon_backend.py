from __future__ import annotations

import pytest

from galvo_gui.motion.base import GalvoError
from galvo_gui.motion.canon.backend import CanonGalvoBackend


class FakeRS232:
    def __init__(self) -> None:
        self.calls = []
        self._status = {1: True, 2: True}

    def connect(self, port=None, timeout_s=0.5) -> None:
        self.calls.append(("connect", port))

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def clear_error(self, axis: int) -> None:
        self.calls.append(("clear_error", axis))

    def servo_on(self, axis: int) -> None:
        self.calls.append(("servo_on", axis))

    def servo_off(self, axis: int) -> None:
        self.calls.append(("servo_off", axis))

    def home(self, axis: int) -> None:
        self.calls.append(("home", axis))

    def read_status(self, axis: int):
        self.calls.append(("read_status", axis))
        return type("Status", (), {"sync": self._status[axis]})()

    def switch_high_speed(self, axis: int) -> None:
        self.calls.append(("switch_high_speed", axis))

    def switch_rs232(self, axis: int) -> None:
        self.calls.append(("switch_rs232", axis))

    def read_position(self, axis: int, target_mode: int = 0) -> int:
        return 1000 * axis


class FakeMotion:
    def __init__(self) -> None:
        self.calls = []
        self.current = (123, -456)
        self.target = (123, -456)

    def initialize(self, board_index: int = 0, program_file=None) -> None:
        self.calls.append(("initialize", board_index, program_file))

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))

    def start(self) -> None:
        self.calls.append(("start",))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def read_current_xy_bits(self):
        self.calls.append(("read_current_xy_bits",))
        return self.current

    def read_target_xy_bits(self):
        self.calls.append(("read_target_xy_bits",))
        return self.target

    def update_positions(self, x_bits: int, y_bits: int) -> None:
        self.calls.append(("update_positions", x_bits, y_bits))
        self.current = (x_bits, y_bits)
        self.target = (x_bits, y_bits)


def test_connect_runs_canon_startup_sequence_in_order() -> None:
    rs = FakeRS232()
    motion = FakeMotion()
    backend = CanonGalvoBackend(rs232=rs, motion=motion, bit_scale_nm=1.0)

    backend.connect("COM7")

    assert motion.calls[:2] == [("initialize", 0, None), ("start",)]
    assert rs.calls == [
        ("connect", "COM7"),
        ("clear_error", 1),
        ("clear_error", 2),
        ("servo_on", 1),
        ("servo_on", 2),
        ("read_status", 1),
        ("read_status", 2),
        ("switch_high_speed", 1),
        ("switch_high_speed", 2),
    ]


def test_connect_homes_unsynced_axes() -> None:
    rs = FakeRS232()
    rs._status[2] = False
    backend = CanonGalvoBackend(rs232=rs, motion=FakeMotion(), bit_scale_nm=1.0)

    backend.connect("COM3")

    assert ("home", 2) in rs.calls


def test_disconnect_runs_reverse_sequence() -> None:
    rs = FakeRS232()
    motion = FakeMotion()
    backend = CanonGalvoBackend(rs232=rs, motion=motion, bit_scale_nm=1.0)
    backend.connect("COM2")

    backend.disconnect()

    assert rs.calls[-5:] == [
        ("switch_rs232", 1),
        ("switch_rs232", 2),
        ("servo_off", 1),
        ("servo_off", 2),
        ("disconnect",),
    ]
    assert motion.calls[-2:] == [("stop",), ("shutdown",)]


def test_move_relative_accumulates_from_target_position_not_stale_current_position() -> None:
    class MotionWithStaleCurrent(FakeMotion):
        def __init__(self) -> None:
            super().__init__()
            self.target = (0, 0)

        def read_current_xy_bits(self):
            self.calls.append(("read_current_xy_bits",))
            return (0, 0)

        def read_target_xy_bits(self):
            self.calls.append(("read_target_xy_bits",))
            return self.target

        def update_positions(self, x_bits: int, y_bits: int) -> None:
            self.calls.append(("update_positions", x_bits, y_bits))
            self.target = (x_bits, y_bits)

    motion = MotionWithStaleCurrent()
    backend = CanonGalvoBackend(rs232=FakeRS232(), motion=motion, bit_scale_nm=1.0)
    backend._connected = True

    backend.move_relative(100.0, 0.0)
    backend.move_relative(100.0, 0.0)

    assert [call for call in motion.calls if call[0] == "update_positions"] == [
        ("update_positions", 100, 0),
        ("update_positions", 200, 0),
    ]


def test_scan_is_rejected_for_canon_backend() -> None:
    backend = CanonGalvoBackend(rs232=FakeRS232(), motion=FakeMotion(), bit_scale_nm=1.0)

    with pytest.raises(GalvoError, match="scan imaging is disabled"):
        backend.scan(1.0, 1.0, 1, 1, 0.0, 0.0, lambda *_: None, lambda: False)


def test_set_home_redefines_origin_and_goto_center() -> None:
    motion = FakeMotion()
    backend = CanonGalvoBackend(rs232=FakeRS232(), motion=motion, bit_scale_nm=2.0)
    backend._connected = True

    assert backend.set_home() == (246.0, -912.0)
    assert backend.read_xy_nm() == (0.0, 0.0)

    motion.current = (140, -400)
    motion.target = (140, -400)
    assert backend.read_xy_nm() == (34.0, 112.0)

    backend.goto_center()
    assert motion.calls[-1] == ("update_positions", 123, -456)

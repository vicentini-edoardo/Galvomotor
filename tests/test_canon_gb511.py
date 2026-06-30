from __future__ import annotations

import pytest

from galvo_gui.motion.base import GalvoError
from galvo_gui.motion.canon.gb511 import GB511MotionController


class FakeDll:
    def __init__(self) -> None:
        self.calls = []

    def gb511_open(self, board_index):
        self.calls.append(("gb511_open", board_index))
        return 0

    def gb511_close(self):
        self.calls.append(("gb511_close",))
        return 0

    def ctr_reset_param(self):
        self.calls.append(("ctr_reset_param",))
        return 0

    def ctr_get_current_xy_pos(self, x_ptr, y_ptr):
        self.calls.append(("ctr_get_current_xy_pos",))
        x_ptr._obj.value = 123  # type: ignore[attr-defined]
        y_ptr._obj.value = -456  # type: ignore[attr-defined]
        return 0


def test_initialize_opens_board_and_resets_params(tmp_path) -> None:
    fake = FakeDll()
    motion = GB511MotionController(dll_loader=lambda _: fake)

    motion.initialize(board_index=2)

    assert fake.calls[:2] == [
        ("gb511_open", 2),
        ("ctr_reset_param",),
    ]


def test_read_current_xy_bits_returns_two_ints() -> None:
    fake = FakeDll()
    motion = GB511MotionController(dll_loader=lambda _: fake)
    motion.initialize()

    xy = motion.read_current_xy_bits()

    assert xy == (123, -456)


def test_failing_dll_call_raises_galvo_error() -> None:
    class BrokenDll(FakeDll):
        def gb511_open(self, board_index):
            return 5

    motion = GB511MotionController(dll_loader=lambda _: BrokenDll())

    with pytest.raises(GalvoError, match="gb511_open"):
        motion.initialize()

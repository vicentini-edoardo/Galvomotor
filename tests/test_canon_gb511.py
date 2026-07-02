from __future__ import annotations

import codecs

import pytest

from galvo_gui.motion.base import GalvoError
from galvo_gui.motion.canon import gb511
from galvo_gui.motion.canon.gb511 import GB511MotionController

try:
    codecs.lookup("mbcs")
except LookupError:
    # "mbcs" is a Windows-only codec; alias it to utf-8 so tests can run on
    # the non-Windows CI/dev boxes this suite is also exercised on.
    codecs.register(lambda name: codecs.lookup("utf-8") if name == "mbcs" else None)


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

    def ctr_load_program_file(self, path):
        self.calls.append(("ctr_load_program_file", path))
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


def test_initialize_auto_loads_program_file_from_config_files(tmp_path, monkeypatch) -> None:
    default_program_file = tmp_path / "gbdsp.hex"
    default_program_file.write_bytes(b"")
    monkeypatch.setattr(gb511, "_DEFAULT_PROGRAM_FILE_PATH", default_program_file)
    fake = FakeDll()
    motion = GB511MotionController(dll_loader=lambda _: fake)

    motion.initialize()

    assert fake.calls[-1] == ("ctr_load_program_file", str(default_program_file).encode("mbcs"))


def test_initialize_skips_program_file_when_not_found_in_config_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gb511, "_DEFAULT_PROGRAM_FILE_PATH", tmp_path / "missing.hex")
    fake = FakeDll()
    motion = GB511MotionController(dll_loader=lambda _: fake)

    motion.initialize()

    assert all(call[0] != "ctr_load_program_file" for call in fake.calls)


def test_explicit_program_file_overrides_config_files_default(tmp_path, monkeypatch) -> None:
    default_program_file = tmp_path / "gbdsp.hex"
    default_program_file.write_bytes(b"")
    monkeypatch.setattr(gb511, "_DEFAULT_PROGRAM_FILE_PATH", default_program_file)
    fake = FakeDll()
    motion = GB511MotionController(dll_loader=lambda _: fake)

    motion.initialize(program_file="explicit.hex")

    assert fake.calls[-1] == ("ctr_load_program_file", b"explicit.hex")


def test_failing_dll_call_raises_galvo_error() -> None:
    class BrokenDll(FakeDll):
        def gb511_open(self, board_index):
            return 5

    motion = GB511MotionController(dll_loader=lambda _: BrokenDll())

    with pytest.raises(GalvoError, match="gb511_open"):
        motion.initialize()

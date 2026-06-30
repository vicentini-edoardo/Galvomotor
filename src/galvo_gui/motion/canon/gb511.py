from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Callable

from galvo_gui.motion.base import GalvoError

_DEFAULT_DLL_PATH = Path(__file__).resolve().parents[4] / "config_files" / "CanonGB511.dll"
_DEFAULT_DLL_LOADER = getattr(ctypes, "WinDLL", ctypes.CDLL)


class GB511MotionController:
    def __init__(
        self,
        dll_path: str = str(_DEFAULT_DLL_PATH),
        dll_loader: Callable[[str], object] = _DEFAULT_DLL_LOADER,
    ) -> None:
        self._dll_path = dll_path
        self._dll_loader = dll_loader
        self._dll = None
        self._started = False

    def initialize(self, board_index: int = 0, program_file: str | None = None) -> None:
        self._dll = self._dll_loader(self._dll_path)
        self._call("gb511_open", board_index)
        self._call("ctr_reset_param")
        if program_file:
            self._call("ctr_load_program_file", program_file.encode("mbcs"))

    def shutdown(self) -> None:
        if self._dll is None:
            return
        self._call("gb511_close")
        self._dll = None
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def load_waveform(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self._started = False

    def update_positions(self, x_bits: int, y_bits: int) -> None:
        self._call("ctr_goto_xy", x_bits, y_bits)

    def read_current_xy_bits(self) -> tuple[int, int]:
        x = ctypes.c_long()
        y = ctypes.c_long()
        self._call("ctr_get_current_xy_pos", ctypes.byref(x), ctypes.byref(y))
        return (int(x.value), int(y.value))

    def read_target_xy_bits(self) -> tuple[int, int]:
        x = ctypes.c_long()
        y = ctypes.c_long()
        self._call("ctr_get_target_xy_pos", ctypes.byref(x), ctypes.byref(y))
        return (int(x.value), int(y.value))

    def _call(self, name: str, *args):
        if self._dll is None:
            raise GalvoError(f"GB511 controller is not initialized for {name}.")
        func = getattr(self._dll, name, None)
        if func is None:
            raise GalvoError(f"CanonGB511.dll missing function {name}.")
        result = func(*args)
        if isinstance(result, int) and result != 0:
            raise GalvoError(f"{name} failed with code {result}.")
        return result

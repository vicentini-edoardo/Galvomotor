from __future__ import annotations

import contextlib

from galvo_gui.motion.canon.rs232 import CanonRS232
from galvo_gui.motion.galvo_nea import GalvoNeaBackend, _DEFAULT_CAL_FILES_PATH

_AXES = (1, 2)


class CanonGalvoBackend(GalvoNeaBackend):
    """Full real backend plus Canon RS-232 mode management.

    This backend keeps the same functionality as ``GalvoNeaBackend`` for
    XY motion, Z motion, signal readout, and scans. Its extra job is to
    bring the Canon controller into high-speed mode over RS-232 so the
    existing ``galvo_functions`` / GB511 path can drive the galvomotor.
    """

    def __init__(
        self,
        cal_files_path: str = str(_DEFAULT_CAL_FILES_PATH),
        rs232: CanonRS232 | None = None,
        board_index: int = 0,
        program_file: str | None = None,
        serial_port: str | None = None,
    ) -> None:
        super().__init__(cal_files_path)
        self._rs232 = rs232 or CanonRS232()
        self._board_index = board_index
        self._program_file = program_file
        self._serial_port = serial_port
        self._rs232_connected = False

    def connect(self, host: str = "nea-server") -> None:
        galvo_open = False
        try:
            self._connect_nea_session(host)
            self._open_galvo_hardware()
            galvo_open = True
            self._open_rs232()
            self._prepare_axes_for_high_speed()
            self._complete_connect()
        except Exception:
            self._unwind_failed_connect(galvo_open=galvo_open)
            raise

    def disconnect(self) -> None:
        try:
            self._restore_rs232_mode()
        finally:
            super().disconnect()

    def _open_rs232(self) -> None:
        self._rs232.connect(port=self._serial_port)
        self._rs232_connected = True

    def _prepare_axes_for_high_speed(self) -> None:
        for axis in _AXES:
            self._rs232.clear_error(axis)
        for axis in _AXES:
            self._rs232.servo_on(axis)
        for axis in _AXES:
            if not self._rs232.read_status(axis).sync:
                self._rs232.home(axis)
        for axis in _AXES:
            self._rs232.switch_high_speed(axis)

    def _restore_rs232_mode(self) -> None:
        if not self._rs232_connected:
            return
        for axis in _AXES:
            with contextlib.suppress(Exception):
                self._rs232.switch_rs232(axis)
        for axis in _AXES:
            with contextlib.suppress(Exception):
                self._rs232.servo_off(axis)
        with contextlib.suppress(Exception):
            self._rs232.disconnect()
        self._rs232_connected = False

    def _unwind_failed_connect(self, *, galvo_open: bool) -> None:
        self._connected = False
        self._restore_rs232_mode()
        self._gb511_wrap = None
        self._galvo = None
        self._mirror_cls = None
        self._stream_module = None
        self._context = None
        self._z_nm = 0.0
        if galvo_open:
            # No board-close API is available in the legacy galvo_functions
            # path; drop references and unwind the neaSNOM session instead.
            self._disconnect_nea_session()
        else:
            with contextlib.suppress(Exception):
                self._disconnect_nea_session()

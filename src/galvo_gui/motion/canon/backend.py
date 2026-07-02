from __future__ import annotations

import contextlib
import time

from galvo_gui.motion.base import GalvoError
from galvo_gui.motion.canon.rs232 import CanonRS232
from galvo_gui.motion.galvo_nea import _DEFAULT_CAL_FILES_PATH, GalvoNeaBackend

_AXES = (1, 2)
_HOMING_TIMEOUT_S = 30.0
_HOMING_POLL_S = 0.2


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
        board_index: int | None = None,
        program_file: str | None = None,
        serial_port: str | None = None,
    ) -> None:
        super().__init__(cal_files_path)
        self._backend_label = "Canon"
        self._rs232 = rs232 or CanonRS232()
        self._board_index = board_index
        self._program_file = program_file
        self._serial_port = serial_port
        self._rs232_connected = False

    def connect(self, host: str = "nea-server") -> None:
        galvo_open = False
        try:
            self._report_status(f"Starting connection to {host}.")
            self._report_status("Opening neaSNOM session...")
            self._connect_nea_session(host)
            self._report_status("neaSNOM session ready.")
            self._report_status("Opening galvo hardware...")
            self._open_galvo_hardware()
            galvo_open = True
            self._report_status("Galvo hardware ready.")
            self._report_status("Opening Canon RS-232 link...")
            self._open_rs232()
            self._report_status("Canon RS-232 link ready.")
            self._report_status("Preparing Canon axes for high-speed mode...")
            self._prepare_axes_for_high_speed()
            self._report_status("Canon axes ready in high-speed mode.")
            self._report_status("Validating hardware read-back...")
            self._complete_connect()
            self._report_status("Connection complete.")
        except Exception:
            self._report_status("Connection failed; unwinding partial session...")
            self._unwind_failed_connect(galvo_open=galvo_open)
            raise

    def disconnect(self) -> None:
        try:
            self._report_status("Restoring Canon RS-232 mode...")
            self._restore_rs232_mode()
        finally:
            super().disconnect()

    def _open_rs232(self) -> None:
        self._rs232.connect(port=self._serial_port)
        self._rs232_connected = True

    def _prepare_axes_for_high_speed(self) -> None:
        self._report_status("Clearing Canon axis errors...")
        for axis in _AXES:
            self._rs232.clear_error(axis)
        self._report_status("Enabling Canon servos...")
        for axis in _AXES:
            self._rs232.servo_on(axis)
        self._report_status("Checking Canon axis sync state...")
        for axis in _AXES:
            if not self._rs232.read_status(axis).sync:
                self._report_status(f"Axis {axis} not synced; starting homing.")
                self._rs232.home(axis)
                self._wait_axis_synced(axis)
        self._report_status("Switching Canon axes to high-speed mode...")
        for axis in _AXES:
            self._rs232.switch_high_speed(axis)

    def _wait_axis_synced(self, axis: int) -> None:
        # home() only starts origin detection. Switching the driver to
        # high-speed mode while it is still origining leaves it not following
        # the GB511, with the position read-back dead (sentinel on both axes).
        deadline = time.monotonic() + _HOMING_TIMEOUT_S
        while True:
            status = self._rs232.read_status(axis)
            if status.sync and not getattr(status, "origining", False):
                self._report_status(f"Axis {axis} homing complete.")
                return
            if time.monotonic() >= deadline:
                raise GalvoError(
                    f"Axis {axis} did not report sync within "
                    f"{_HOMING_TIMEOUT_S:.0f} s after homing was started."
                )
            time.sleep(_HOMING_POLL_S)

    def _restore_rs232_mode(self) -> None:
        if not self._rs232_connected:
            return
        self._report_status("Switching Canon axes back to RS-232 mode...")
        for axis in _AXES:
            with contextlib.suppress(Exception):
                self._rs232.switch_rs232(axis)
        self._report_status("Turning Canon servos off...")
        for axis in _AXES:
            with contextlib.suppress(Exception):
                self._rs232.servo_off(axis)
        with contextlib.suppress(Exception):
            self._rs232.disconnect()
        self._rs232_connected = False
        self._report_status("Canon RS-232 link closed.")

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

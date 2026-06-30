from __future__ import annotations

import contextlib
from typing import Callable

from galvo_gui.motion.base import STANDARD_STEP_OPTIONS_NM, GalvoBackend, GalvoError, SnomSample
from galvo_gui.motion.canon.gb511 import GB511MotionController
from galvo_gui.motion.canon.rs232 import CanonRS232


class CanonGalvoBackend(GalvoBackend):
    def __init__(
        self,
        rs232: CanonRS232 | None = None,
        motion: GB511MotionController | None = None,
        bit_scale_nm: float = 1.0,
        board_index: int = 0,
        program_file: str | None = None,
        serial_port: str | None = None,
    ) -> None:
        self._rs232 = rs232 or CanonRS232()
        self._motion = motion or GB511MotionController()
        self._bit_scale_nm = bit_scale_nm
        self._board_index = board_index
        self._program_file = program_file
        self._serial_port = serial_port
        self._connected = False

    def connect(self, host: str = "") -> None:
        opened_motion = False
        opened_serial = False
        try:
            self._motion.initialize(board_index=self._board_index, program_file=self._program_file)
            opened_motion = True
            port = host or self._serial_port
            self._rs232.connect(port=port)
            opened_serial = True
            for axis in (1, 2):
                self._rs232.clear_error(axis)
            for axis in (1, 2):
                self._rs232.servo_on(axis)
            for axis in (1, 2):
                if not self._rs232.read_status(axis).sync:
                    self._rs232.home(axis)
            self._motion.start()
            for axis in (1, 2):
                self._rs232.switch_high_speed(axis)
        except Exception:
            if opened_serial:
                self._safe_disconnect_serial()
            if opened_motion:
                self._safe_shutdown_motion()
            raise
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._motion.stop()
        for axis in (1, 2):
            self._rs232.switch_rs232(axis)
        for axis in (1, 2):
            self._rs232.servo_off(axis)
        self._rs232.disconnect()
        self._motion.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        self._require_connected()
        x_bits, y_bits = self._motion.read_target_xy_bits()
        self._motion.update_positions(
            int(x_bits + (dx_nm / self._bit_scale_nm)),
            int(y_bits + (dy_nm / self._bit_scale_nm)),
        )

    def move_z_relative(self, dz_nm: float) -> None:
        raise GalvoError("Canon backend does not support Z motion.")

    def read_xy_nm(self) -> tuple[float, float]:
        self._require_connected()
        x_bits, y_bits = self._motion.read_current_xy_bits()
        return (x_bits * self._bit_scale_nm, y_bits * self._bit_scale_nm)

    def read_z_nm(self) -> float:
        return 0.0

    def goto_center(self) -> None:
        self._require_connected()
        self._motion.update_positions(0, 0)

    def available_xy_steps_nm(self) -> tuple[float, ...]:
        return STANDARD_STEP_OPTIONS_NM

    def available_z_steps_nm(self) -> tuple[float, ...]:
        return ()

    def read_sample(self, t_integ_s: float = 0.05) -> SnomSample:
        raise GalvoError("Canon backend has no signal readout configured.")

    def scan(
        self,
        dx_nm: float,
        dy_nm: float,
        nb_x: int,
        nb_y: int,
        twait: float,
        t_integ_s: float,
        on_point: Callable[[int, int, SnomSample], None],
        stop_check: Callable[[], bool],
    ) -> None:
        raise GalvoError(
            "Canon motion is available, but scan imaging is disabled "
            "until a signal-readout source is added."
        )

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("Canon backend is not connected.")

    def _safe_disconnect_serial(self) -> None:
        with contextlib.suppress(Exception):
            self._rs232.disconnect()

    def _safe_shutdown_motion(self) -> None:
        with contextlib.suppress(Exception):
            self._motion.shutdown()

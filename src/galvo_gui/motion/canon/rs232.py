from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

try:
    from serial import Serial as _PySerial
    from serial.tools import list_ports

    _SERIAL_AVAILABLE = True
except ImportError:
    _PySerial = None
    _SERIAL_AVAILABLE = False

    class _MissingListPorts:
        @staticmethod
        def comports() -> list[object]:
            return []

    list_ports = _MissingListPorts()

from galvo_gui.motion.base import GalvoError


@dataclass(frozen=True)
class CanonStatus:
    raw: int
    servo_on: bool
    sync: bool
    in_position: bool
    alarm: bool
    origining: bool
    program_coordinates: bool
    z_phase_warning: bool
    moving: bool
    encoder_warning: bool
    target_relative: bool
    target_position_set: bool
    linearity_calibrating: bool


@dataclass(frozen=True)
class CanonErrors:
    raw: int
    stroke_over: bool
    counter_over: bool
    clock_lack: bool
    driver_overheat: bool
    motor_overheat: bool
    format_error: bool
    command_data_error: bool
    parameter_error: bool
    status_error: bool
    communication_error: bool
    origin_detection_error: bool
    encoder_signal_error: bool
    servo_off_by_hardware: bool
    current_saturation: bool


class CanonRS232:
    def __init__(
        self,
        serial_factory: Callable[..., Any] | None = None,
        port_selector: Callable[[], str] | None = None,
    ) -> None:
        self._serial_factory = serial_factory or _PySerial
        self._port_selector = port_selector or self.auto_detect_port
        self._ser = None

    def connect(self, port: str | None = None, timeout_s: float = 0.5) -> None:
        if self._serial_factory is None:
            raise GalvoError("pyserial is not installed; Canon RS-232 is unavailable.")
        selected = port or self._port_selector()
        if not selected:
            raise GalvoError("No Canon RS-232 port found.")
        self._ser = self._serial_factory(
            port=selected,
            baudrate=38400,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )

    def disconnect(self) -> None:
        if self._ser is not None:
            self._ser.close()
        self._ser = None

    def clear_error(self, axis: int) -> None:
        self._expect_ok(axis, 1, None)

    def home(self, axis: int) -> None:
        self._expect_ok(axis, 2, None)

    def servo_on(self, axis: int) -> None:
        self._expect_ok(axis, 4, 1)

    def servo_off(self, axis: int) -> None:
        self._expect_ok(axis, 4, 0)

    def switch_high_speed(self, axis: int) -> None:
        self._expect_ok(axis, 23, 7)

    def switch_rs232(self, axis: int) -> None:
        self._expect_ok(axis, 23, 0)

    def read_status(self, axis: int) -> CanonStatus:
        raw = self._send_command(axis, 14, None)
        return CanonStatus(
            raw=raw,
            servo_on=bool(raw & 0x0001),
            sync=bool(raw & 0x0002),
            in_position=bool(raw & 0x0004),
            alarm=bool(raw & 0x0008),
            origining=bool(raw & 0x0010),
            program_coordinates=bool(raw & 0x0020),
            z_phase_warning=bool(raw & 0x0040),
            moving=bool(raw & 0x0100),
            encoder_warning=bool(raw & 0x0400),
            target_relative=bool(raw & 0x1000),
            target_position_set=bool(raw & 0x4000),
            linearity_calibrating=bool(raw & 0x8000),
        )

    def read_errors(self, axis: int) -> CanonErrors:
        raw = self._send_command(axis, 15, None)
        return CanonErrors(
            raw=raw,
            stroke_over=bool(raw & 0x0001),
            counter_over=bool(raw & 0x0002),
            clock_lack=bool(raw & 0x0008),
            driver_overheat=bool(raw & 0x0010),
            motor_overheat=bool(raw & 0x0020),
            format_error=bool(raw & 0x0040),
            command_data_error=bool(raw & 0x0080),
            parameter_error=bool(raw & 0x0100),
            status_error=bool(raw & 0x0200),
            communication_error=bool(raw & 0x0400),
            origin_detection_error=bool(raw & 0x0800),
            encoder_signal_error=bool(raw & 0x1000),
            servo_off_by_hardware=bool(raw & 0x4000),
            current_saturation=bool(raw & 0x8000),
        )

    def read_position(self, axis: int, target_mode: int = 0) -> int:
        return self._send_command(axis, 12, target_mode)

    def read_temperature(self, axis: int, source: int) -> float:
        raw = self._send_command(axis, 11, source)
        if source in (10, 11):
            return raw / 100.0
        return float(raw)

    def read_version(self, axis: int, source: int) -> int:
        return self._send_command(axis, 13, source)

    def read_parameter(self, axis: int, parameter_id: int) -> int:
        return self._send_parameter(axis, parameter_id, None)

    def write_parameter(self, axis: int, parameter_id: int, value: int) -> None:
        result = self._send_parameter(axis, parameter_id, value)
        if result != 0:
            raise GalvoError(f"Parameter write failed for axis {axis}, parameter {parameter_id}.")

    def auto_detect_port(self) -> str:
        for port in list_ports.comports():
            text = " ".join(filter(None, [port.device, port.description, port.hwid]))
            if "usb" in text.lower() or "serial" in text.lower() or "canon" in text.lower():
                return port.device
        return ""

    def _expect_ok(self, axis: int, command_id: int, data: int | None) -> None:
        result = self._send_command(axis, command_id, data)
        if result != 0:
            raise GalvoError(f"Canon command {command_id} failed on axis {axis}.")

    def _send_command(self, axis: int, command_id: int, data: int | None) -> int:
        payload = f"A{axis}C{command_id:03d}/"
        if data is not None:
            payload += f"{data}"
        frame = (payload + "\n").encode("ascii")
        return self._exchange(frame, axis, "C", command_id)

    def _send_parameter(self, axis: int, parameter_id: int, data: int | None) -> int:
        payload = f"A{axis}P{parameter_id:03d}/"
        if data is not None:
            payload += f"{data}"
        frame = (payload + "\n").encode("ascii")
        return self._exchange(frame, axis, "P", parameter_id)

    def _exchange(self, frame: bytes, axis: int, kind: str, ident: int) -> int:
        if self._ser is None:
            raise GalvoError("Canon RS-232 is not connected.")
        self._ser.write(frame)
        reply = self._ser.readline().decode("ascii", errors="replace").strip()
        prefix = f"A{axis}{kind}{ident:03d}/"
        if not reply.startswith(prefix):
            raise GalvoError(f"Unexpected reply: {reply!r}")
        try:
            return int(reply.split("/", 1)[1])
        except ValueError as exc:
            raise GalvoError(f"Malformed reply: {reply!r}") from exc

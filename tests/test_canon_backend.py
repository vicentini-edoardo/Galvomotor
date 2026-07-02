from __future__ import annotations

from types import MethodType

import pytest

from galvo_gui.motion import galvo_nea
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


def _make_backend(monkeypatch, rs232: FakeRS232 | None = None) -> CanonGalvoBackend:
    monkeypatch.setattr(galvo_nea, "GALVO_AVAILABLE", True)
    return CanonGalvoBackend(
        cal_files_path="cal_files",
        rs232=rs232 or FakeRS232(),
        board_index=3,
        program_file="program.hex",
        serial_port="COM7",
    )


def test_connect_runs_real_backend_startup_plus_canon_rs232_sequence(monkeypatch) -> None:
    rs = FakeRS232()
    backend = _make_backend(monkeypatch, rs)
    calls: list[tuple[str, object]] = []

    def fake_connect_nea_session(self, host: str) -> None:
        calls.append(("connect_nea_session", host))

    def fake_open_galvo_hardware(self) -> None:
        calls.append(("open_galvo_hardware", None))

    def fake_complete_connect(self) -> None:
        calls.append(("complete_connect", None))
        self._connected = True

    backend._connect_nea_session = MethodType(fake_connect_nea_session, backend)
    backend._open_galvo_hardware = MethodType(fake_open_galvo_hardware, backend)
    backend._complete_connect = MethodType(fake_complete_connect, backend)

    backend.connect("nea-host")

    assert calls == [
        ("connect_nea_session", "nea-host"),
        ("open_galvo_hardware", None),
        ("complete_connect", None),
    ]
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
    assert backend.is_connected() is True


def test_connect_homes_unsynced_axes_before_high_speed(monkeypatch) -> None:
    rs = FakeRS232()
    rs._status[2] = False
    backend = _make_backend(monkeypatch, rs)

    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)
    monkeypatch.setattr(backend, "_complete_connect", lambda: setattr(backend, "_connected", True))

    backend.connect("nea-host")

    assert ("home", 2) in rs.calls
    assert rs.calls.index(("home", 2)) < rs.calls.index(("switch_high_speed", 1))


def test_disconnect_restores_rs232_mode_before_real_backend_teardown(monkeypatch) -> None:
    rs = FakeRS232()
    backend = _make_backend(monkeypatch, rs)
    backend._connected = True
    backend._rs232_connected = True
    backend._gb511_wrap = object()
    backend._mirror_cls = object()
    backend._stream_module = object()
    backend._context = object()

    teardown = []

    def fake_disconnect_nea_session() -> None:
        teardown.append("disconnect_nea_session")

    monkeypatch.setattr(backend, "_disconnect_nea_session", fake_disconnect_nea_session)

    backend.disconnect()

    assert rs.calls == [
        ("switch_rs232", 1),
        ("switch_rs232", 2),
        ("servo_off", 1),
        ("servo_off", 2),
        ("disconnect",),
    ]
    assert teardown == ["disconnect_nea_session"]
    assert backend.is_connected() is False


def test_failed_connect_unwinds_rs232_and_nea_session(monkeypatch) -> None:
    rs = FakeRS232()
    backend = _make_backend(monkeypatch, rs)
    teardown = []

    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: teardown.append(("nea", host)))
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: teardown.append(("galvo", None)))
    monkeypatch.setattr(
        backend,
        "_complete_connect",
        lambda: (_ for _ in ()).throw(RuntimeError("readback failed")),
    )

    def fake_disconnect_nea_session() -> None:
        teardown.append(("disconnect_nea_session", None))

    monkeypatch.setattr(backend, "_disconnect_nea_session", fake_disconnect_nea_session)

    with pytest.raises(RuntimeError, match="readback failed"):
        backend.connect("nea-host")

    assert teardown == [
        ("nea", "nea-host"),
        ("galvo", None),
        ("disconnect_nea_session", None),
    ]
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
        ("switch_rs232", 1),
        ("switch_rs232", 2),
        ("servo_off", 1),
        ("servo_off", 2),
        ("disconnect",),
    ]
    assert backend.is_connected() is False


def test_canon_connect_reports_stage_progress(monkeypatch) -> None:
    rs = FakeRS232()
    backend = _make_backend(monkeypatch, rs)
    messages: list[str] = []
    backend.set_status_callback(messages.append)

    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)
    monkeypatch.setattr(backend, "_complete_connect", lambda: setattr(backend, "_connected", True))

    backend.connect("nea-host")

    assert messages == [
        "Canon: Starting connection to nea-host.",
        "Canon: Opening neaSNOM session...",
        "Canon: neaSNOM session ready.",
        "Canon: Opening galvo hardware...",
        "Canon: Galvo hardware ready.",
        "Canon: Opening Canon RS-232 link...",
        "Canon: Canon RS-232 link ready.",
        "Canon: Preparing Canon axes for high-speed mode...",
        "Canon: Clearing Canon axis errors...",
        "Canon: Enabling Canon servos...",
        "Canon: Checking Canon axis sync state...",
        "Canon: Switching Canon axes to high-speed mode...",
        "Canon: Canon axes ready in high-speed mode.",
        "Canon: Validating hardware read-back...",
        "Canon: Connection complete.",
    ]

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
        self._status[axis] = True  # homing completes instantly in the fake

    def read_status(self, axis: int):
        self.calls.append(("read_status", axis))
        return type("Status", (), {"sync": self._status[axis], "origining": False})()

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

    monkeypatch.setattr(
        backend, "_connect_nea_session", lambda host: teardown.append(("nea", host))
    )
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


def test_connect_waits_for_homing_to_finish_before_high_speed(monkeypatch) -> None:
    """home() only starts origin detection: switching the driver to high-speed
    mode while it is still origining leaves it not following the GB511 (dead
    read-back after a reconnect). The switch must wait for sync."""

    class SlowHomingRS232(FakeRS232):
        def __init__(self, polls_needed: int) -> None:
            super().__init__()
            self._polls_needed = polls_needed
            self._homing_axis: int | None = None

        def home(self, axis: int) -> None:
            self.calls.append(("home", axis))
            self._homing_axis = axis  # stays unsynced until polled enough

        def read_status(self, axis: int):
            self.calls.append(("read_status", axis))
            if axis == self._homing_axis:
                self._polls_needed -= 1
                if self._polls_needed <= 0:
                    self._status[axis] = True
            return type(
                "Status",
                (),
                {"sync": self._status[axis], "origining": not self._status[axis]},
            )()

    import galvo_gui.motion.canon.backend as canon_backend_module

    monkeypatch.setattr(canon_backend_module.time, "sleep", lambda _s: None)

    rs = SlowHomingRS232(polls_needed=3)
    rs._status[2] = False
    backend = _make_backend(monkeypatch, rs)
    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)
    monkeypatch.setattr(backend, "_complete_connect", lambda: setattr(backend, "_connected", True))

    backend.connect("nea-host")

    polls_for_axis_2 = [c for c in rs.calls if c == ("read_status", 2)]
    assert len(polls_for_axis_2) >= 3  # waited through the homing
    last_poll_index = len(rs.calls) - 1 - rs.calls[::-1].index(("read_status", 2))
    assert last_poll_index < rs.calls.index(("switch_high_speed", 1))
    assert backend.is_connected() is True


def test_connect_fails_and_unwinds_when_homing_never_syncs(monkeypatch) -> None:
    from galvo_gui.motion.base import GalvoError

    class NeverSyncRS232(FakeRS232):
        def home(self, axis: int) -> None:
            self.calls.append(("home", axis))  # sync is never reached

    import galvo_gui.motion.canon.backend as canon_backend_module

    monkeypatch.setattr(canon_backend_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(canon_backend_module, "_HOMING_TIMEOUT_S", 0.0)

    rs = NeverSyncRS232()
    rs._status[1] = False
    backend = _make_backend(monkeypatch, rs)
    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)

    with pytest.raises(GalvoError, match="did not report sync"):
        backend.connect("nea-host")

    # Failed connect unwinds: axes back to RS-232 mode, servos off.
    assert ("switch_rs232", 1) in rs.calls
    assert ("servo_off", 1) in rs.calls
    assert backend.is_connected() is False

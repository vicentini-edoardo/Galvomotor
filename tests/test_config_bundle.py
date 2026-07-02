"""Tests for local hardware config bundle path resolution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from galvo_gui.motion import galvo_nea
from galvo_gui.motion.base import GalvoError


def test_config_bundle_paths_point_to_repo_root() -> None:
    """Real backend defaults should resolve from the repo root."""
    repo_root = Path(__file__).resolve().parents[1]

    assert repo_root / "config_files" == galvo_nea._CONFIG_DIR
    assert repo_root / "config_files" / "cal_files" == galvo_nea._DEFAULT_CAL_FILES_PATH
    assert galvo_nea._DEFAULT_CORRECTION_FILE == "GM-2020-ftheta-10mm-fo4.tsc"


_CX = 0.11080  # goto units per read pulse, from the notebook's galvo_move


class _FakeWrapper:
    """GB511 stand-in with the board's two coordinate spaces: reads report
    fine pulses, ctr_goto_xy takes coarse goto units (ratio _CX)."""

    def __init__(self, drop_moves: bool = False) -> None:
        self._x_bit = 1000
        self._y_bit = 2000
        self.goto_calls: list[tuple[int, int]] = []
        self._drop_moves = drop_moves

    def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
        x.value = self._x_bit  # type: ignore[attr-defined]
        y.value = self._y_bit  # type: ignore[attr-defined]

    def ctr_goto_xy(self, gx: int, gy: int) -> None:
        self.goto_calls.append((gx, gy))
        if self._drop_moves:
            return  # axis railed at its limit: command has no effect
        self._x_bit = round(gx / _CX)
        self._y_bit = round(gy / _CX)


class _FakeGalvo:
    """galvo_functions.Galvo stand-in: only calibration values are used."""

    K = 1.79  # read pulses per nm
    X0 = 400.0  # home, nm
    Y0 = -250.0

    @staticmethod
    def Bit2Pos(bits: int) -> float:
        return bits / _FakeGalvo.K


def test_move_relative_uses_notebook_goto_unit_conversion(monkeypatch) -> None:
    """move_relative must replicate the working notebook's galvo_move: absolute
    target = current read pulses + K*delta, commanded as int(CX * target).
    Vendor Galvo.Move skips the CX conversion (targets ~9x out of range) and is
    absolute-from-home, so repeated jogs would not accumulate."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()
    backend._gb511_wrap = wrapper

    backend.move_relative(100.0, -50.0)

    assert wrapper.goto_calls == [
        (int(_CX * (1000 + 1.79 * 100.0)), int(_CX * (2000 + 1.79 * -50.0))),
    ]

    # Second jog accumulates from the new position instead of re-homing.
    x_bit, y_bit = wrapper._x_bit, wrapper._y_bit
    backend.move_relative(100.0, 0.0)
    assert wrapper.goto_calls[-1] == (
        int(_CX * (x_bit + 1.79 * 100.0)),
        int(_CX * (y_bit + 1.79 * 0.0)),
    )


def test_move_relative_raises_when_axis_does_not_follow(monkeypatch) -> None:
    """A railed/ignored axis must surface as an error via read-back, not no-op."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()
    backend._gb511_wrap = _FakeWrapper(drop_moves=True)

    with pytest.raises(GalvoError, match="produced no motion"):
        backend.move_relative(100.0, 0.0)


def test_move_relative_skips_readback_below_goto_resolution(monkeypatch) -> None:
    """A step that quantises onto the current goto-unit target cannot be
    verified by read-back and must not be reported as a dropped move."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()
    backend._gb511_wrap = _FakeWrapper(drop_moves=True)

    backend.move_relative(0.1, 0.0)  # 0.179 read pulses ≈ 0.02 goto units


def test_goto_center_converts_home_to_goto_units() -> None:
    """goto_center must not use Galvo.GoHome (no CX conversion): home is
    K*X0 read pulses, commanded as int(CX * K * X0) goto units."""

    wrapper = _FakeWrapper()
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()
    backend._gb511_wrap = wrapper

    backend.goto_center()

    assert wrapper.goto_calls == [
        (int(_CX * 1.79 * 400.0), int(_CX * 1.79 * -250.0)),
    ]


def test_set_home_redefines_origin_and_goto_center() -> None:
    wrapper = _FakeWrapper()
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()
    backend._gb511_wrap = wrapper

    assert backend.set_home() == (
        pytest.approx((1000 / 1.79) - 400.0),
        pytest.approx((2000 / 1.79) - (-250.0)),
    )
    assert backend.read_xy_nm() == (pytest.approx(0.0), pytest.approx(0.0))

    backend.goto_center()
    assert wrapper.goto_calls[-1] == (int(_CX * 1000), int(_CX * 2000))


def test_available_xy_steps_require_a_full_goto_unit() -> None:
    """Steps below the goto-unit resolution (~5 nm at K=1.79) are unusable."""

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo()

    assert backend.available_xy_steps_nm() == (50.0, 100.0, 500.0, 1000.0)


def test_available_xy_steps_disable_sub_resolution_moves() -> None:
    assert galvo_nea._available_xy_steps_nm(1.79) == (50.0, 100.0, 500.0, 1000.0)


def test_read_gb511_bits_passes_byref_to_raw_dll_handles(monkeypatch) -> None:
    """Raw ctypes DLL handles must receive pointers, not c_long values:
    passing by value leaves the out-params at 0 bits forever."""

    class FakeDll:
        def ctr_get_current_xy_pos(self, x_ref: object, y_ref: object) -> int:
            # byref() products expose the wrapped object as ._obj
            x_ref._obj.value = 1234  # type: ignore[attr-defined]
            y_ref._obj.value = -567  # type: ignore[attr-defined]
            return 0

    monkeypatch.setattr(galvo_nea, "_is_raw_dll", lambda obj: True)
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._gb511_wrap = FakeDll()

    assert backend._read_gb511_bits() == (1234, -567)


def test_gb511_nonzero_return_code_raises_galvo_error() -> None:
    class FakeWrapper:
        def ctr_get_current_xy_pos(self, x: object, y: object) -> int:
            return 5  # board refuses the command

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._gb511_wrap = FakeWrapper()

    with pytest.raises(GalvoError, match="ctr_get_current_xy_pos failed with code 5"):
        backend._read_gb511_bits()


def test_gb511_native_exception_wrapped_as_galvo_error() -> None:
    class FakeWrapper:
        def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
            raise OSError("exception: access violation writing 0x0000000000000000")

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._gb511_wrap = FakeWrapper()

    with pytest.raises(GalvoError, match="ctr_get_current_xy_pos failed"):
        backend._read_gb511_bits()


def test_z_reads_use_cached_position_and_moves_refresh_cache() -> None:
    class FakeMirror:
        instances = 0

        def __init__(self) -> None:
            type(self).instances += 1
            self.absolute_position = [0.0, 0.0, 100.0]
            self.relative_moves: list[tuple[float, float, float]] = []

        def __enter__(self) -> FakeMirror:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def go_relative(self, dx: float, dy: float, dz: float) -> None:
            self.relative_moves.append((dx, dy, dz))
            self.absolute_position[2] += dz

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._mirror_cls = FakeMirror
    backend._loop = None
    backend._z0_nm = 100.0
    backend._z_nm = 0.0

    assert backend.read_z_nm() == 0.0
    assert FakeMirror.instances == 0
    backend.move_z_relative(25.0)
    assert backend.read_z_nm() == 25.0
    assert FakeMirror.instances == 1


def test_z_move_reports_hardware_readback_not_requested_delta() -> None:
    """If the mirror stalls, the GUI must see the real position, not dead reckoning."""

    class StalledMirror:
        def __init__(self) -> None:
            self.absolute_position = [0.0, 0.0, 100.0]

        def __enter__(self) -> StalledMirror:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def go_relative(self, dx: float, dy: float, dz: float) -> None:
            pass  # motor never moves

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._mirror_cls = StalledMirror
    backend._loop = None
    backend._z0_nm = 100.0
    backend._z_nm = 0.0

    backend.move_z_relative(500.0)

    assert backend.read_z_nm() == 0.0


def test_disconnect_keeps_the_nea_session_alive(monkeypatch) -> None:
    """Regression: nea_tools cannot re-connect inside one process — after a
    nea_tools.disconnect(), the next session hangs in the mirror path (Z
    reference read at connect, or the first Z move). A GUI disconnect must
    therefore leave the SDK session untouched; it dies with the process."""
    awaited = {"disconnect_called": False, "loop_closed": False}

    async def fake_disconnect() -> None:
        awaited["disconnect_called"] = True

    class FakeLoop:
        def __init__(self) -> None:
            self._closed = False

        def is_closed(self) -> bool:
            return self._closed

        def close(self) -> None:
            awaited["loop_closed"] = True
            self._closed = True

    monkeypatch.setattr(
        galvo_nea,
        "nea_tools",
        SimpleNamespace(disconnect=fake_disconnect),
        raising=False,
    )

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._status_callback = None
    backend._loop = FakeLoop()
    backend._gb511_wrap = object()
    backend._mirror_cls = object()

    backend.disconnect()

    assert awaited["disconnect_called"] is False  # session left live on purpose
    assert awaited["loop_closed"] is False
    assert backend._connected is False
    assert backend._loop is None


def test_real_connect_reports_stage_progress(monkeypatch) -> None:
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    messages: list[str] = []
    backend._status_callback = messages.append
    backend._backend_label = "Real"

    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)
    monkeypatch.setattr(backend, "_complete_connect", lambda: None)

    backend.connect("nea-host")

    assert messages == [
        "Real: Starting connection to nea-host.",
        "Real: Opening neaSNOM session...",
        "Real: neaSNOM session ready.",
        "Real: Opening galvo hardware...",
        "Real: Galvo hardware ready.",
        "Real: Validating hardware read-back...",
        "Real: Connection complete.",
    ]


def test_open_galvo_hardware_reuses_the_board_handle_across_reconnects(monkeypatch) -> None:
    """Regression: the GB511 board is single-client and galvo_functions has no
    close API, so re-running open_galvo() on a reconnect reprogrammed the live
    board and killed the read-back (both axes stuck at the -2**19 sentinel).
    The handle from the first open must be reused for the rest of the process."""

    open_calls: list[dict] = []
    board = object()

    def fake_open_galvo(CalFn="", idx_board=1, program_file=None):
        open_calls.append({"CalFn": CalFn, "idx_board": idx_board})
        return board, 0

    monkeypatch.setattr(galvo_nea, "_open_galvo", fake_open_galvo, raising=False)
    monkeypatch.setattr(galvo_nea, "_GalvoHW", lambda cal: SimpleNamespace(), raising=False)
    monkeypatch.setattr(galvo_nea, "_GB511_SHARED", {"wrap": None, "kwargs": None})

    first = object.__new__(galvo_nea.GalvoNeaBackend)
    first._cal_path = "cal"
    first._status_callback = None
    first._open_galvo_hardware()

    second = object.__new__(galvo_nea.GalvoNeaBackend)  # reconnect
    second._cal_path = "cal"
    second._status_callback = None
    second._open_galvo_hardware()

    assert len(open_calls) == 1
    assert first._gb511_wrap is board
    assert second._gb511_wrap is board

    # Different board settings are not served from the cache.
    third = object.__new__(galvo_nea.GalvoNeaBackend)
    third._cal_path = "cal"
    third._status_callback = None
    third._board_index = 5
    third._open_galvo_hardware()
    assert len(open_calls) == 2


def test_reconnect_adopts_the_live_nea_session(monkeypatch) -> None:
    """Regression: nea_tools does not survive disconnect→connect inside one
    process — the second session hung in the mirror path (Z reference read
    at connect, or the first Z move). The session opened first must stay
    live and be adopted, without another nea_tools.connect, by every later
    connection to the same host."""

    import sys

    connect_calls: list[str] = []
    disconnect_calls: list[bool] = []

    async def fake_connect(host, fingerprint=None, path_to_dll=""):
        connect_calls.append(host)

    async def fake_disconnect():
        disconnect_calls.append(True)

    applied: list[object] = []
    monkeypatch.setattr(
        galvo_nea,
        "nea_tools",
        SimpleNamespace(connect=fake_connect, disconnect=fake_disconnect),
        raising=False,
    )
    monkeypatch.setattr(
        galvo_nea, "nest_asyncio", SimpleNamespace(apply=applied.append), raising=False
    )
    fresh_session = {
        "loop": None,
        "host": None,
        "connected": False,
        "context": None,
        "stream": None,
        "mirror_cls": None,
    }
    monkeypatch.setattr(galvo_nea, "_NEA_SESSION", fresh_session)

    mirror_cls = object
    microscope = SimpleNamespace(
        stream=SimpleNamespace(), motors=SimpleNamespace(Mirror=mirror_cls)
    )
    monkeypatch.setitem(sys.modules, "neaspec", SimpleNamespace(context=object()))
    monkeypatch.setitem(sys.modules, "nea_tools", SimpleNamespace(microscope=microscope))
    monkeypatch.setitem(sys.modules, "nea_tools.microscope", microscope)
    monkeypatch.setitem(sys.modules, "nea_tools.microscope.stream", microscope.stream)
    monkeypatch.setitem(sys.modules, "nea_tools.microscope.motors", microscope.motors)

    first = object.__new__(galvo_nea.GalvoNeaBackend)
    first._status_callback = None
    first._connect_nea_session("nea-host")
    loop = first._loop
    assert loop is not None and not loop.is_closed()
    assert connect_calls == ["nea-host"]

    first._disconnect_nea_session()
    assert disconnect_calls == []  # session left live on purpose
    assert not loop.is_closed()

    second = object.__new__(galvo_nea.GalvoNeaBackend)  # reconnect
    second._status_callback = None
    second._connect_nea_session("nea-host")

    assert connect_calls == ["nea-host"]  # no second nea_tools.connect
    assert second._loop is loop
    assert second._mirror_cls is mirror_cls  # SDK objects adopted
    assert applied == [loop]  # nest_asyncio applied exactly once

    # A different host is the one case that really tears down and reconnects.
    third = object.__new__(galvo_nea.GalvoNeaBackend)
    third._status_callback = None
    third._connect_nea_session("other-host")
    assert disconnect_calls == [True]
    assert connect_calls == ["nea-host", "other-host"]

    loop.close()  # test hygiene only; the app keeps it open


def test_complete_connect_rejects_a_dead_position_readback() -> None:
    """A reconnect into a board whose read-back reports the -2**19 sentinel on
    both axes must fail loudly instead of connecting into hardware that
    silently ignores every move."""

    class SentinelWrapper:
        def ctr_get_current_xy_pos(self, x, y) -> None:
            x.value = -(2**19)
            y.value = -(2**19)

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._gb511_wrap = SentinelWrapper()
    backend._status_callback = None

    with pytest.raises(GalvoError, match="not live"):
        backend._complete_connect()

    assert not getattr(backend, "_connected", False)

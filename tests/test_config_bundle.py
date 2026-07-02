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
    backend._z_reference_ready = True

    assert backend.read_z_nm() == 0.0
    assert FakeMirror.instances == 0
    backend.move_z_relative(25.0)
    assert backend.read_z_nm() == 25.0
    assert FakeMirror.instances == 1


def test_complete_connect_does_not_touch_mirror_reference(monkeypatch) -> None:
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._status_callback = None
    backend._connected = False
    backend._z0_nm = 123.0
    backend._z_nm = 456.0
    backend._z_reference_ready = True
    backend._read_gb511_bits = lambda: (1, 2)
    backend._read_absolute_mirror_z_nm = lambda: (_ for _ in ()).throw(
        AssertionError("mirror should not be touched during connect")
    )

    backend._complete_connect()

    assert backend._connected is True
    assert backend._z0_nm == 0.0
    assert backend._z_nm == 0.0
    assert backend._z_reference_ready is False


def test_move_z_relative_captures_reference_lazily() -> None:
    class FakeMirror:
        instances = 0

        def __init__(self) -> None:
            type(self).instances += 1
            self.absolute_position = [0.0, 0.0, 100.0]

        def __enter__(self) -> FakeMirror:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def go_relative(self, dx: float, dy: float, dz: float) -> None:
            self.absolute_position[2] += dz

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._status_callback = None
    backend._connected = True
    backend._mirror_cls = FakeMirror
    backend._loop = None
    backend._z0_nm = 0.0
    backend._z_nm = 0.0
    backend._z_reference_ready = False

    backend.move_z_relative(25.0)

    assert backend.read_z_nm() == 25.0
    assert backend._z0_nm == 100.0
    assert backend._z_reference_ready is True
    assert FakeMirror.instances == 2


def test_open_mirror_binds_backend_loop(monkeypatch) -> None:
    calls: list[object] = []

    class FakeLoop:
        def is_closed(self) -> bool:
            return False

    class FakeMirror:
        def __init__(self) -> None:
            calls.append("mirror")

    monkeypatch.setattr(galvo_nea.asyncio, "set_event_loop", lambda loop: calls.append(loop))

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._loop = FakeLoop()
    backend._mirror_cls = FakeMirror

    mirror = backend._open_mirror()

    assert isinstance(mirror, FakeMirror)
    assert isinstance(calls[0], FakeLoop)
    assert calls[1] == "mirror"


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


def test_disconnect_awaits_nea_tools_on_backend_loop(monkeypatch) -> None:
    awaited = {
        "disconnect_called": False,
        "run_until_complete_called": False,
        "loop_closed": False,
    }

    async def fake_disconnect() -> None:
        awaited["disconnect_called"] = True

    class FakeLoop:
        def __init__(self) -> None:
            self._closed = False

        def run_until_complete(self, awaitable):
            awaited["run_until_complete_called"] = True
            import asyncio

            return asyncio.run(awaitable)

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
    backend._loop = FakeLoop()
    backend._gb511_wrap = object()
    backend._mirror_cls = object()

    backend.disconnect()

    assert awaited["run_until_complete_called"] is True
    assert awaited["disconnect_called"] is True
    assert awaited["loop_closed"] is True
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

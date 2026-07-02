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


class _FakeWrapper:
    """Raw-DLL stand-in whose bit positions only vendor Move may change."""

    def __init__(self) -> None:
        self._x_bit = 1000
        self._y_bit = 2000

    def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
        x.value = self._x_bit  # type: ignore[attr-defined]
        y.value = self._y_bit  # type: ignore[attr-defined]


class _FakeGalvo:
    """Vendor galvo_functions.Galvo stand-in: Move(dx, dy, wrap) is relative."""

    K = 1.79

    def __init__(self, drop_moves: bool = False) -> None:
        self.move_calls: list[tuple[float, float, object]] = []
        self._drop_moves = drop_moves

    def Move(self, dx_nm: float, dy_nm: float, wrap: object) -> None:  # noqa: N802
        self.move_calls.append((dx_nm, dy_nm, wrap))
        if self._drop_moves:
            return  # Xmax guard: silently ignores the request
        wrap._x_bit += round(self.K * dx_nm)  # type: ignore[attr-defined]
        wrap._y_bit += round(self.K * dy_nm)  # type: ignore[attr-defined]


def test_move_relative_delegates_to_vendor_move(monkeypatch) -> None:
    """move_relative must go through galvo_functions.Galvo.Move: driving the raw
    DLL's ctr_goto_xy with a guessed prototype mis-routed the axis arguments
    (both X and Y jogs moved the same mirror with inconsistent step sizes)."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    galvo = _FakeGalvo()
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = galvo
    backend._gb511_wrap = wrapper

    backend.move_relative(100.0, -50.0)
    backend.move_relative(100.0, 0.0)

    assert galvo.move_calls == [
        (100.0, -50.0, wrapper),
        (100.0, 0.0, wrapper),
    ]
    assert (wrapper._x_bit, wrapper._y_bit) == (
        1000 + 2 * round(1.79 * 100.0),
        2000 + round(1.79 * -50.0),
    )


def test_move_relative_raises_when_vendor_move_silently_drops(monkeypatch) -> None:
    """Galvo.Move's Xmax guard drops moves without an error; the backend must
    detect the missing motion via read-back and raise instead of no-oping."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo(drop_moves=True)
    backend._gb511_wrap = _FakeWrapper()

    with pytest.raises(GalvoError, match="produced no motion"):
        backend.move_relative(100.0, 0.0)


def test_move_relative_skips_readback_for_sub_bit_moves(monkeypatch) -> None:
    """A requested step below 1 bit cannot be verified by read-back and must
    not be reported as a dropped move."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = _FakeGalvo(drop_moves=True)
    backend._gb511_wrap = _FakeWrapper()

    backend.move_relative(0.1, 0.0)  # 0.179 bits — rounds to zero


def test_available_xy_steps_disable_sub_resolution_moves() -> None:
    assert galvo_nea._available_xy_steps_nm(1.79) == (1.0, 10.0, 100.0)


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


def test_disconnect_awaits_nea_tools_on_backend_loop(monkeypatch) -> None:
    awaited = {"disconnect_called": False, "run_until_complete_called": False}

    async def fake_disconnect() -> None:
        awaited["disconnect_called"] = True

    class FakeLoop:
        def run_until_complete(self, awaitable):
            awaited["run_until_complete_called"] = True
            import asyncio

            return asyncio.run(awaitable)

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
    assert backend._connected is False

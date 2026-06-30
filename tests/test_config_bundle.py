"""Tests for local hardware config bundle path resolution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from galvo_gui.motion import galvo_nea


def test_config_bundle_paths_point_to_repo_root() -> None:
    """Real backend defaults should resolve from the repo root."""
    repo_root = Path(__file__).resolve().parents[1]

    assert galvo_nea._CONFIG_DIR == repo_root / "config_files"
    assert galvo_nea._DEFAULT_CAL_FILES_PATH == repo_root / "config_files" / "cal_files"
    assert galvo_nea._DEFAULT_CORRECTION_FILE == "GM-2020-ftheta-10mm-fo4.tsc"


def test_move_relative_bypasses_galvo_move_and_uses_bit_arithmetic() -> None:
    """move_relative must bypass galvo_functions.Move (Xmax=1500 guard silently drops moves)
    and operate directly in bit space: new_bit = current_bit + K * delta_nm."""

    class FakeWrapper:
        def __init__(self) -> None:
            self.goto_calls: list[tuple[int, int]] = []
            self._x_bit = 1000
            self._y_bit = 2000

        def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
            x.value = self._x_bit  # type: ignore[attr-defined]
            y.value = self._y_bit  # type: ignore[attr-defined]

        def ctr_goto_xy(self, xb: int, yb: int) -> None:
            self.goto_calls.append((xb, yb))
            self._x_bit = xb
            self._y_bit = yb

    class FakeGalvo:
        K = 1.79

    wrapper = FakeWrapper()
    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = FakeGalvo()
    backend._gb511_wrap = wrapper

    backend.move_relative(100.0, -50.0)
    backend.move_relative(100.0, 0.0)

    assert wrapper.goto_calls == [
        (round(1000 + 1.79 * 100.0), round(2000 + 1.79 * -50.0)),
        (round(round(1000 + 1.79 * 100.0) + 1.79 * 100.0), round(round(2000 + 1.79 * -50.0) + 1.79 * 0.0)),
    ]


def test_available_xy_steps_disable_sub_resolution_moves() -> None:
    assert galvo_nea._available_xy_steps_nm(1.79) == (1.0, 10.0, 100.0)


def test_z_reads_use_cached_position_and_moves_refresh_cache() -> None:
    class FakeMirror:
        instances = 0

        def __init__(self) -> None:
            type(self).instances += 1
            self.absolute_position = [0.0, 0.0, 100.0]
            self.relative_moves: list[tuple[float, float, float]] = []

        def __enter__(self) -> "FakeMirror":
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

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


def test_move_relative_translates_to_home_relative_target() -> None:
    """Compatibility shim should adapt the local galvo_functions API."""

    class FakeGalvo:
        def __init__(self) -> None:
            self.calls: list[tuple[float, float, object]] = []

        def Move(self, x_nm: float, y_nm: float, wrapper: object) -> None:
            self.calls.append((x_nm, y_nm, wrapper))

    backend = object.__new__(galvo_nea.GalvoNeaBackend)
    backend._connected = True
    backend._galvo = FakeGalvo()
    backend._gb511_wrap = object()
    backend.read_xy_nm = lambda: (100.0, 200.0)

    backend.move_relative(10.0, -20.0)

    assert backend._galvo.calls == [(110.0, 180.0, backend._gb511_wrap)]


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

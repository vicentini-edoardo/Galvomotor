"""Tests for local hardware config bundle path resolution."""

from __future__ import annotations

from pathlib import Path

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

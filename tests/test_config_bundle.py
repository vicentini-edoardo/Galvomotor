"""Tests for the real galvo/neaSNOM backends and config bundle path resolution."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
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
    fine pulses, ctr_goto_xy takes coarse goto units (ratio _CX).  The read
    pulse count quantises downward, the worst case for the read→goto
    conversion (the encoder never reports a fraction of a pulse)."""

    def __init__(
        self,
        drop_moves: bool = False,
        *,
        clamp_x_to: int | None = None,
        x_after_bias: int = 0,
    ) -> None:
        self._x_bit = 1000
        self._y_bit = 2000
        self.goto_calls: list[tuple[int, int]] = []
        self._drop_moves = drop_moves
        self._clamp_x_to = clamp_x_to
        self._x_after_bias = x_after_bias

    def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
        x.value = self._x_bit  # type: ignore[attr-defined]
        y.value = self._y_bit  # type: ignore[attr-defined]

    def ctr_goto_xy(self, gx: int, gy: int) -> None:
        self.goto_calls.append((gx, gy))
        if self._drop_moves:
            return  # axis railed at its limit: command has no effect
        self._x_bit = math.floor(gx / _CX)
        self._y_bit = math.floor(gy / _CX)
        self._x_bit += self._x_after_bias
        if self._clamp_x_to is not None:
            self._x_bit = min(self._x_bit, self._clamp_x_to)


class _DelayedFollowWrapper(_FakeWrapper):
    """First read after a command still shows the old-ish position, then catches up."""

    def __init__(self, x_first_read_bias: int = -112) -> None:
        super().__init__()
        self._x_first_read_bias = x_first_read_bias
        self._pending_x_bit: int | None = None
        self._pending_y_bit: int | None = None
        self._lag_reads_remaining = 0

    def ctr_goto_xy(self, gx: int, gy: int) -> None:
        self.goto_calls.append((gx, gy))
        self._pending_x_bit = math.floor(gx / _CX)
        self._pending_y_bit = math.floor(gy / _CX)
        self._lag_reads_remaining = 1

    def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
        if self._lag_reads_remaining > 0 and self._pending_x_bit is not None:
            x.value = self._pending_x_bit + self._x_first_read_bias  # type: ignore[attr-defined]
            y.value = self._pending_y_bit  # type: ignore[attr-defined]
            self._lag_reads_remaining -= 1
            return
        if self._pending_x_bit is not None:
            self._x_bit = self._pending_x_bit
            self._y_bit = self._pending_y_bit if self._pending_y_bit is not None else self._y_bit
            self._pending_x_bit = None
            self._pending_y_bit = None
        x.value = self._x_bit  # type: ignore[attr-defined]
        y.value = self._y_bit  # type: ignore[attr-defined]


class _FakeGalvo:
    """galvo_functions.Galvo stand-in: only calibration values are used."""

    K = 1.79  # read pulses per nm
    X0 = 400.0  # home, nm
    Y0 = -250.0

    @staticmethod
    def Bit2Pos(bits: int) -> float:
        return bits / _FakeGalvo.K


def _make_galvo(wrapper: object | None = None, galvo: object | None = None):
    backend = object.__new__(galvo_nea.RealGalvoBackend)
    backend._connected = True
    backend._status_callback = None
    if galvo is not None:
        backend._galvo = galvo
    if wrapper is not None:
        backend._gb511_wrap = wrapper
    return backend


def _make_nea():
    backend = object.__new__(galvo_nea.RealNeaBackend)
    backend._connected = True
    backend._status_callback = None
    return backend


class _FakeStreamValues:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def __getitem__(self, index: int) -> float:
        if index != -1 or not self._values:
            raise IndexError
        return self._values.pop(0)


class _FakeStream:
    def __init__(self, values: dict[str, list[float]]) -> None:
        self.data = {key: _FakeStreamValues(list(series)) for key, series in values.items()}

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeStreamModule:
    def __init__(self, values: dict[str, list[float]]) -> None:
        self._values = values

    def Stream(self) -> _FakeStream:  # noqa: N802 - mirrors nea_tools API
        return _FakeStream(self._values)


def _stream_values(
    amp: list[float] | None = None,
    phase: list[float] | None = None,
) -> dict[str, list[float]]:
    return {
        **{f"O{h}A": list(amp or []) for h in range(galvo_nea._N_HARMONICS)},
        **{f"O{h}P": list(phase or []) for h in range(galvo_nea._N_HARMONICS)},
    }


def test_nea_read_sample_uses_monotonic_time_and_keeps_zero_amplitudes(monkeypatch) -> None:
    backend = _make_nea()
    backend._stream_module = _FakeStreamModule(
        _stream_values(amp=[0.0, 0.0], phase=[0.1, 0.1])
    )
    monkeypatch.setattr(galvo_nea.time, "time", lambda: (_ for _ in ()).throw(RuntimeError("wall clock used")))
    ticks = iter([0.0, 0.0, 0.02, 0.04])
    monkeypatch.setattr(galvo_nea.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)

    sample = backend.read_sample(0.03)

    assert sample.o_amp[0] == 0.0


def test_nea_read_sample_uses_circular_phase_mean(monkeypatch) -> None:
    backend = _make_nea()
    backend._stream_module = _FakeStreamModule(
        _stream_values(amp=[1.0, 1.0], phase=[math.pi - 0.01, -math.pi + 0.01])
    )
    ticks = iter([0.0, 0.0, 0.02, 0.04])
    monkeypatch.setattr(galvo_nea.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)

    sample = backend.read_sample(0.03)

    assert abs(abs(sample.o_phase[0]) - math.pi) < 0.02


def test_nea_read_sample_warns_when_stream_has_no_data(monkeypatch) -> None:
    messages: list[str] = []
    backend = _make_nea()
    backend._status_callback = messages.append
    backend._stream_module = _FakeStreamModule(_stream_values())
    ticks = iter([0.0, 0.0, 0.02])
    monkeypatch.setattr(galvo_nea.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)

    sample = backend.read_sample(0.01)

    assert np.isnan(sample.o_amp).all()
    assert np.isnan(sample.o_phase).all()
    assert any("no neaSNOM stream data" in message for message in messages)


def test_move_relative_uses_notebook_goto_unit_conversion(monkeypatch) -> None:
    """move_relative must replicate the working notebook's galvo_move: absolute
    target = current read pulses + K*delta, commanded as round(CX * target).
    Vendor Galvo.Move skips the CX conversion (targets ~9x out of range) and is
    absolute-from-home, so repeated jogs would not accumulate."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    backend = _make_galvo(wrapper, _FakeGalvo())

    backend.move_relative(100.0, -50.0)

    assert wrapper.goto_calls == [
        (round(_CX * (1000 + 1.79 * 100.0)), round(_CX * (2000 + 1.79 * -50.0))),
    ]

    # Second jog accumulates from the new position instead of re-homing.
    x_bit, y_bit = wrapper._x_bit, wrapper._y_bit
    backend.move_relative(100.0, 0.0)
    assert wrapper.goto_calls[-1] == (
        round(_CX * (x_bit + 1.79 * 100.0)),
        round(_CX * (y_bit + 1.79 * 0.0)),
    )


def test_single_axis_jogs_do_not_disturb_the_other_axis(monkeypatch) -> None:
    """Regression: the read→goto conversion used int() truncation, and since
    ctr_goto_xy commands both axes, every X jog re-derived the Y target one
    goto unit low about half the time (int(CX * round(g / CX)) == g - 1).
    On hardware this showed up as X and Y motion being mixed together, with
    the parked axis creeping on every jog of the other one."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    wrapper.ctr_goto_xy(111, 222)  # park at a position the board can report
    wrapper.goto_calls.clear()
    backend = _make_galvo(wrapper, _FakeGalvo())

    y_bit_start = wrapper._y_bit
    for _ in range(10):
        backend.move_relative(100.0, 0.0)
    assert wrapper._y_bit == y_bit_start
    assert {gy for _gx, gy in wrapper.goto_calls} == {222}


def test_back_and_forth_jogs_return_to_the_same_position(monkeypatch) -> None:
    """Regression: int() truncation biased every commanded target downward, so
    a +step/-step pair lost one goto unit (~9 read pulses) per round trip and
    the galvo could not be walked back and forth across the same positions."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    wrapper.ctr_goto_xy(111, 222)  # park at a position the board can report
    wrapper.goto_calls.clear()
    backend = _make_galvo(wrapper, _FakeGalvo())

    x_bit_start = wrapper._x_bit
    backend.move_relative(500.0, 0.0)
    x_bit_forward = wrapper._x_bit
    for _ in range(5):
        backend.move_relative(-500.0, 0.0)
        assert wrapper._x_bit == x_bit_start
        backend.move_relative(500.0, 0.0)
        assert wrapper._x_bit == x_bit_forward


def test_parked_axis_holds_commanded_goto_despite_readback_noise(monkeypatch) -> None:
    """Regression: ctr_goto_xy commands both axes, so a single-axis jog must
    hold the parked axis at its last commanded goto unit. Re-deriving it from
    a noisy read-back (round(CX * bits)) lets encoder noise near a goto-unit
    boundary flip it, which showed up as X and Y being coupled."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    backend = _make_galvo(wrapper, _FakeGalvo())

    backend.move_relative(0.0, 100.0)  # establish a Y command
    cmd_gy = backend._cmd_gy

    # Perturb the Y read-back by ~half a goto unit so a re-quantisation would
    # round to a different goto unit than the one we actually commanded.
    wrapper._y_bit += 6
    assert round(_CX * wrapper._y_bit) != cmd_gy  # old code would have drifted

    for _ in range(5):
        backend.move_relative(100.0, 0.0)  # jog X only

    # Every X jog re-commanded Y to the stored goto unit, never a re-quantised one.
    assert {gy for _gx, gy in wrapper.goto_calls[1:]} == {cmd_gy}


def test_move_relative_raises_when_axis_does_not_follow(monkeypatch) -> None:
    """A railed/ignored axis must surface as an error via read-back, not no-op."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = _make_galvo(_FakeWrapper(drop_moves=True), _FakeGalvo())

    with pytest.raises(GalvoError, match="produced no motion"):
        backend.move_relative(100.0, 0.0)


def test_move_relative_raises_when_x_readback_misses_commanded_target(monkeypatch) -> None:
    """A move that gets clamped on X must fail instead of being reported as success."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = _make_galvo(_FakeWrapper(clamp_x_to=1100), _FakeGalvo())

    with pytest.raises(GalvoError, match="X axis read-back"):
        backend.move_relative(200.0, 0.0)


def test_move_relative_accepts_small_x_readback_quantisation_error(monkeypatch) -> None:
    """A small pulse miss within half a goto-unit is normal read-back quantisation."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = _make_galvo(_FakeWrapper(x_after_bias=-5), _FakeGalvo())

    backend.move_relative(100.0, 0.0)


def test_validate_axis_follow_uses_configured_tolerance() -> None:
    backend = _make_galvo(galvo=_FakeGalvo())
    backend._axis_follow_tolerance_pulses = 7

    backend._validate_axis_follow(
        100.0,
        0.0,
        1100.0,
        2000.0,
        1106,
        2000,
    )

    with pytest.raises(GalvoError, match="X axis read-back"):
        backend._validate_axis_follow(
            100.0,
            0.0,
            1100.0,
            2000.0,
            1108,
            2000,
        )


def test_move_relative_applies_configured_x_goto_bias(monkeypatch) -> None:
    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    wrapper = _FakeWrapper()
    backend = _make_galvo(wrapper, _FakeGalvo())
    backend._x_goto_bias = 12
    # The +12 goto-unit bias lands the read-back ~108 pulses off target, so this
    # exercises the miss path. Pin a tight tolerance here rather than relying on
    # the module default, which is deliberately wide (~130) to tolerate that same
    # bias/offset residual on real hardware.
    backend._axis_follow_tolerance_pulses = 15

    with pytest.raises(GalvoError, match="X axis read-back"):
        backend.move_relative_pulses(100.0, 0.0)

    assert wrapper.goto_calls[-1] == (
        round(_CX * (1000 + 100.0)) + 12,
        round(_CX * 2000),
    )


def test_move_relative_waits_for_delayed_x_follow(monkeypatch) -> None:
    """A move should tolerate one stale readback sample if the next read catches up."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = _make_galvo(_DelayedFollowWrapper(), _FakeGalvo())

    backend.move_relative(1000.0, 0.0)


def test_move_relative_skips_readback_below_goto_resolution(monkeypatch) -> None:
    """A step that quantises onto the current goto-unit target cannot be
    verified by read-back and must not be reported as a dropped move."""

    monkeypatch.setattr(galvo_nea.time, "sleep", lambda _s: None)
    backend = _make_galvo(_FakeWrapper(drop_moves=True), _FakeGalvo())

    backend.move_relative(0.1, 0.0)  # 0.179 read pulses ≈ 0.02 goto units


def test_goto_center_converts_home_to_goto_units() -> None:
    """goto_center must not use Galvo.GoHome (no CX conversion): home is
    K*X0 read pulses, commanded as round(CX * K * X0) goto units."""

    wrapper = _FakeWrapper()
    backend = _make_galvo(wrapper, _FakeGalvo())

    backend.goto_center()

    assert wrapper.goto_calls == [
        (round(_CX * 1.79 * 400.0), round(_CX * 1.79 * -250.0)),
    ]


def test_set_home_redefines_origin_and_goto_center() -> None:
    wrapper = _FakeWrapper()
    backend = _make_galvo(wrapper, _FakeGalvo())

    assert backend.set_home() == (
        pytest.approx((1000 / 1.79) - 400.0),
        pytest.approx((2000 / 1.79) - (-250.0)),
    )
    assert backend.read_xy_nm() == (pytest.approx(0.0), pytest.approx(0.0))

    backend.goto_center()
    assert wrapper.goto_calls[-1] == (round(_CX * 1000), round(_CX * 2000))


def test_available_xy_steps_require_a_full_goto_unit() -> None:
    """Pulse steps below the goto-unit resolution (~9 read pulses) are unusable."""

    backend = _make_galvo(galvo=_FakeGalvo())

    assert backend.available_xy_steps_pulses() == (10.0, 50.0, 100.0, 500.0, 1000.0)


def test_available_xy_steps_disable_sub_resolution_moves() -> None:
    assert galvo_nea._available_xy_steps_pulses(galvo_nea._GOTO_PER_READ_X) == (
        10.0,
        50.0,
        100.0,
        500.0,
        1000.0,
    )


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
    backend = _make_galvo(FakeDll())

    assert backend._read_gb511_bits() == (1234, -567)


def test_gb511_nonzero_return_code_raises_galvo_error() -> None:
    class FakeWrapper:
        def ctr_get_current_xy_pos(self, x: object, y: object) -> int:
            return 5  # board refuses the command

    backend = _make_galvo(FakeWrapper())

    with pytest.raises(GalvoError, match="ctr_get_current_xy_pos failed with code 5"):
        backend._read_gb511_bits()


def test_gb511_native_exception_wrapped_as_galvo_error() -> None:
    class FakeWrapper:
        def ctr_get_current_xy_pos(self, x: object, y: object) -> None:
            raise OSError("exception: access violation writing 0x0000000000000000")

    backend = _make_galvo(FakeWrapper())

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

    backend = _make_nea()
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

    backend = _make_nea()
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

    backend = _make_nea()
    backend._loop = FakeLoop()
    backend._stream_module = object()
    backend._context = object()
    backend._mirror_cls = object()

    backend.disconnect()

    assert awaited["disconnect_called"] is False  # session left live on purpose
    assert awaited["loop_closed"] is False
    assert backend._connected is False
    assert backend._loop is None


def test_nea_connect_reports_stage_progress(monkeypatch) -> None:
    backend = object.__new__(galvo_nea.RealNeaBackend)
    messages: list[str] = []
    backend._status_callback = messages.append
    backend._backend_label = "neaSNOM"

    monkeypatch.setattr(backend, "_connect_nea_session", lambda host: None)
    monkeypatch.setattr(backend, "_read_absolute_mirror_z_nm", lambda: 0.0)

    backend.connect("nea-host")

    assert messages == [
        "neaSNOM: Starting connection to nea-host.",
        "neaSNOM: Opening neaSNOM session...",
        "neaSNOM: neaSNOM session ready.",
        "neaSNOM: Capturing mirror Z reference...",
        "neaSNOM: Connection complete.",
    ]


def test_galvo_connect_reports_stage_progress(monkeypatch) -> None:
    backend = object.__new__(galvo_nea.RealGalvoBackend)
    messages: list[str] = []
    backend._status_callback = messages.append
    backend._backend_label = "Galvo"

    monkeypatch.setattr(backend, "_open_galvo_hardware", lambda: None)
    monkeypatch.setattr(backend, "_validate_readback", lambda: None)
    monkeypatch.setattr(backend, "_auto_calibrate_offset", lambda: None)

    backend.connect()

    assert messages == [
        "Galvo: Starting galvo connection.",
        "Galvo: Opening galvo hardware...",
        "Galvo: Galvo hardware ready.",
        "Galvo: Validating hardware read-back...",
        "Galvo: Galvo connection complete.",
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

    first = object.__new__(galvo_nea.RealGalvoBackend)
    first._cal_path = "cal"
    first._status_callback = None
    first._open_galvo_hardware()

    second = object.__new__(galvo_nea.RealGalvoBackend)  # reconnect
    second._cal_path = "cal"
    second._status_callback = None
    second._open_galvo_hardware()

    assert len(open_calls) == 1
    assert first._gb511_wrap is board
    assert second._gb511_wrap is board

    # Different board settings are not served from the cache.
    third = object.__new__(galvo_nea.RealGalvoBackend)
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

    first = object.__new__(galvo_nea.RealNeaBackend)
    first._status_callback = None
    first._connect_nea_session("nea-host")
    loop = first._loop
    assert loop is not None and not loop.is_closed()
    assert connect_calls == ["nea-host"]

    first._disconnect_nea_session()
    assert disconnect_calls == []  # session left live on purpose
    assert not loop.is_closed()

    second = object.__new__(galvo_nea.RealNeaBackend)  # reconnect
    second._status_callback = None
    second._connect_nea_session("nea-host")

    assert connect_calls == ["nea-host"]  # no second nea_tools.connect
    assert second._loop is loop
    assert second._mirror_cls is mirror_cls  # SDK objects adopted
    assert applied == [loop]  # nest_asyncio applied exactly once

    # A different host is the one case that really tears down and reconnects.
    third = object.__new__(galvo_nea.RealNeaBackend)
    third._status_callback = None
    third._connect_nea_session("other-host")
    assert disconnect_calls == [True]
    assert connect_calls == ["nea-host", "other-host"]

    loop.close()  # test hygiene only; the app keeps it open


def test_validate_readback_rejects_a_dead_position_readback() -> None:
    """A reconnect into a board whose read-back reports the -2**19 sentinel on
    both axes must fail loudly instead of connecting into hardware that
    silently ignores every move."""

    class SentinelWrapper:
        def ctr_get_current_xy_pos(self, x, y) -> None:
            x.value = -(2**19)
            y.value = -(2**19)

    backend = object.__new__(galvo_nea.RealGalvoBackend)
    backend._gb511_wrap = SentinelWrapper()
    backend._status_callback = None

    with pytest.raises(GalvoError, match="not live"):
        backend._validate_readback()

    assert not getattr(backend, "_connected", False)

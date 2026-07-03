"""Synthetic galvo and neaSNOM backends for development and tests (no hardware)."""

from __future__ import annotations

import math
import time
from typing import Tuple

import numpy as np

from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_PULSES,
    Z_STEP_OPTIONS_NM,
    GalvoBackend,
    GalvoError,
    NeaBackend,
    SnomSample,
)

_N_HARMONICS = 6
# Mock calibration scale: 1 pulse per nm keeps the nm wrappers numerically
# equal to the pulse values, which keeps the (nm-based) scan tests simple.
_MOCK_PULSES_PER_NM = 1.0


class MockGalvoBackend(GalvoBackend):
    """Simulates galvomotor XY moves in encoder pulses (no hardware required)."""

    def __init__(self) -> None:
        self._connected = False
        self._x_p: float = 0.0
        self._y_p: float = 0.0
        self._home_x_p: float = 0.0
        self._home_y_p: float = 0.0

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "") -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Motion (pulses)
    # ------------------------------------------------------------------

    def move_relative_pulses(self, dx_p: float, dy_p: float) -> None:
        self._require_connected()
        self._x_p += dx_p
        self._y_p += dy_p

    def read_xy_pulses(self) -> Tuple[float, float]:
        self._require_connected()
        return (self._x_p - self._home_x_p, self._y_p - self._home_y_p)

    def set_home_pulses(
        self, x_p: float | None = None, y_p: float | None = None
    ) -> Tuple[float, float]:
        self._require_connected()
        if (x_p is None) != (y_p is None):
            raise ValueError("x_p and y_p must be provided together.")
        if x_p is None or y_p is None:
            self._home_x_p = self._x_p
            self._home_y_p = self._y_p
        else:
            self._home_x_p = float(x_p)
            self._home_y_p = float(y_p)
        return (self._home_x_p, self._home_y_p)

    def goto_center(self) -> None:
        self._require_connected()
        self._x_p = self._home_x_p
        self._y_p = self._home_y_p

    def available_xy_steps_pulses(self) -> tuple[float, ...]:
        return STANDARD_STEP_OPTIONS_PULSES

    def pulses_per_nm(self) -> float:
        return _MOCK_PULSES_PER_NM

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("Mock galvo backend is not connected.")


class MockNeaBackend(NeaBackend):
    """Simulates neaSNOM Z motion and synthetic harmonic signals.

    Signal amplitudes decay with distance from the origin (Gaussian spot), so
    scan maps look non-trivial in tests.  The distance uses the galvo position
    passed to :meth:`read_sample` by the caller.
    """

    def __init__(self, seed: int = 42) -> None:
        self._connected = False
        self._z_nm: float = 0.0
        self._rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str = "mock") -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_z_relative(self, dz_nm: float) -> None:
        self._require_connected()
        self._z_nm += dz_nm

    def read_z_nm(self) -> float:
        self._require_connected()
        return self._z_nm

    def available_z_steps_nm(self) -> tuple[float, ...]:
        return Z_STEP_OPTIONS_NM

    # ------------------------------------------------------------------
    # Signal readout
    # ------------------------------------------------------------------

    def read_sample(
        self,
        t_integ_s: float = 0.05,
        xy_nm: Tuple[float, float] = (0.0, 0.0),
    ) -> SnomSample:
        self._require_connected()
        time.sleep(min(t_integ_s, 0.005))  # fast for tests
        x_nm, y_nm = xy_nm
        r = math.sqrt(x_nm ** 2 + y_nm ** 2)
        decay = math.exp(-r / 3000.0)  # 3 µm characteristic scale

        o_amp = np.array(
            [decay * (1.0 / (h + 1)) + float(self._rng.normal(0, 0.01))
             for h in range(_N_HARMONICS)]
        )
        o_phase = np.array(
            [math.pi * h / _N_HARMONICS + float(self._rng.normal(0, 0.05))
             for h in range(_N_HARMONICS)]
        )
        return SnomSample(
            xy_nm=(x_nm, y_nm),
            o_amp=o_amp,
            o_phase=o_phase,
        )

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("Mock neaSNOM backend is not connected.")

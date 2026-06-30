"""Synthetic galvo backend for development and tests (no hardware required)."""

from __future__ import annotations

import math
import time
from typing import Callable, Tuple

import numpy as np

from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_NM,
    GalvoBackend,
    GalvoError,
    SnomSample,
)

_N_HARMONICS = 6


class MockGalvoBackend(GalvoBackend):
    """Simulates galvo moves and returns synthetic harmonic signals.

    Signal amplitudes decay with distance from the origin (Gaussian spot),
    so scan maps look non-trivial in tests.
    """

    def __init__(self, seed: int = 42) -> None:
        self._connected = False
        self._x_nm: float = 0.0
        self._y_nm: float = 0.0
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

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        self._require_connected()
        self._x_nm += dx_nm
        self._y_nm += dy_nm

    def move_z_relative(self, dz_nm: float) -> None:
        self._require_connected()
        self._z_nm += dz_nm

    def read_xy_nm(self) -> Tuple[float, float]:
        self._require_connected()
        return (self._x_nm, self._y_nm)

    def read_z_nm(self) -> float:
        self._require_connected()
        return self._z_nm

    def goto_center(self) -> None:
        self._require_connected()
        self._x_nm = 0.0
        self._y_nm = 0.0
        self._z_nm = 0.0

    def available_xy_steps_nm(self) -> tuple[float, ...]:
        return STANDARD_STEP_OPTIONS_NM

    def available_z_steps_nm(self) -> tuple[float, ...]:
        return STANDARD_STEP_OPTIONS_NM

    # ------------------------------------------------------------------
    # Signal readout
    # ------------------------------------------------------------------

    def read_sample(self, t_integ_s: float = 0.05) -> SnomSample:
        self._require_connected()
        time.sleep(min(t_integ_s, 0.005))  # fast for tests
        r = math.sqrt(self._x_nm ** 2 + self._y_nm ** 2)
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
            xy_nm=(self._x_nm, self._y_nm),
            o_amp=o_amp,
            o_phase=o_phase,
        )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan(
        self,
        dx_nm: float,
        dy_nm: float,
        nb_x: int,
        nb_y: int,
        twait: float,
        t_integ_s: float,
        on_point: Callable[[int, int, SnomSample], None],
        stop_check: Callable[[], bool],
    ) -> None:
        self._require_connected()

        # Move to start corner
        self.move_relative(-dx_nm / 2.0, -dy_nm / 2.0)
        time.sleep(twait)
        x_start, y_start = self.read_xy_nm()

        step_x = dx_nm / (nb_x - 1) if nb_x > 1 else 0.0
        step_y = dy_nm / (nb_y - 1) if nb_y > 1 else 0.0

        for iy in range(nb_y):
            for ix in range(nb_x):
                if stop_check():
                    break
                sample = self.read_sample(t_integ_s)
                on_point(ix, iy, sample)

                if ix < nb_x - 1:
                    self.move_relative(step_x, 0.0)
                    time.sleep(twait)

            if stop_check():
                break

            if iy < nb_y - 1:
                # End of row: return x to start, step y
                x_curr, _ = self.read_xy_nm()
                self.move_relative(x_start - x_curr, step_y)
                time.sleep(twait)

        # Return to centre
        self.goto_center()

    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise GalvoError("Mock galvo backend is not connected.")

"""Backend contracts and data types for galvo motor + neaSNOM signal readout.

The hardware splits into two independent connections:

* :class:`GalvoBackend` drives the galvomotor **XY** stage.
* :class:`NeaBackend` drives the neaSNOM parabolic-mirror **Z** axis and reads
  the optical signal harmonics.

Each can be connected on its own; the Motion tab enables the XY controls only
while a galvo backend is connected and the Z controls only while a neaSNOM
backend is connected.  A raster scan needs both.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Tuple

import numpy as np

_N_HARMONICS = 6
# The galvomotor is commanded and read in encoder pulses — its native unit.
# Manual XY control works directly in pulses so a jog never round-trips
# through nm (pulse → nm → pulse), which is both lossy and pointless.
STANDARD_STEP_OPTIONS_PULSES = (10.0, 50.0, 100.0, 500.0, 1000.0)
# The parabolic-mirror Z axis needs far coarser jogs than the galvo: the lab
# notebooks step it in ~1000 nm increments, and sub-100 nm requests are within
# the positioner's deadband.
Z_STEP_OPTIONS_NM = (10.0, 100.0, 1000.0, 10000.0)


class GalvoError(RuntimeError):
    """Raised when a galvo or neaSNOM backend reports an unrecoverable error."""


@dataclass
class SnomSample:
    """neaSNOM signals sampled at one galvo position."""

    xy_nm: Tuple[float, float]   # galvo read-back position (nm from center)
    o_amp: np.ndarray            # shape (6,) optical amplitude harmonics 0-5
    o_phase: np.ndarray          # shape (6,) optical phase harmonics 0-5 (radians)
    xy_pulses: Tuple[float, float] = (0.0, 0.0)  # galvo read-back position (encoder pulses from center)


class GalvoBackend(ABC):
    """Abstract interface for galvomotor **XY** motion.

    The canonical unit is **encoder pulses**, the galvo's native command/read
    unit. All move calls are **relative** (pulses) from the current position;
    the origin (0, 0) is the active home/center established at startup or by
    the last ``set_home_pulses`` call.

    The ``*_nm`` helpers below are thin convenience wrappers (a single
    ``pulses_per_nm`` conversion) so callers that think in nanometres — e.g.
    the scan's range spec — can still drive the stage without re-implementing
    the conversion. They must not be used to round-trip a value that is
    already in pulses.
    """

    @abstractmethod
    def connect(self, host: str = "") -> None:
        """Open the galvo hardware.

        *host* is unused by the galvo path (it addresses the board over a
        local link) and is accepted only to keep a uniform connect signature.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Release the galvo hardware connection."""
        ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def move_relative_pulses(self, dx_p: float, dy_p: float) -> None:
        """Move galvo by (dx_p, dy_p) encoder pulses from current position."""
        ...

    @abstractmethod
    def read_xy_pulses(self) -> Tuple[float, float]:
        """Return current galvo position (x, y) in pulses relative to center."""
        ...

    @abstractmethod
    def set_home_pulses(
        self, x_p: float | None = None, y_p: float | None = None
    ) -> Tuple[float, float]:
        """Set the galvo home/center used by ``read_xy_pulses``/``goto_center``.

        When *x_p* and *y_p* are omitted, the backend must capture the current
        physical XY position as the new home. When provided, the coordinates
        are absolute pulses in the backend's startup reference frame, so a
        persisted home can be restored on a later session.
        """
        ...

    @abstractmethod
    def goto_center(self) -> None:
        """Move galvo back to the active home/center position."""
        ...

    @abstractmethod
    def available_xy_steps_pulses(self) -> tuple[float, ...]:
        """Return the supported XY jog step sizes (pulses) that actually move
        the stage — i.e. large enough to change the coarse goto-unit command."""
        ...

    @abstractmethod
    def pulses_per_nm(self) -> float:
        """Encoder pulses per nanometre (the calibration scale K)."""
        ...

    def last_move_diagnostics(self) -> dict[str, int | float]:
        """Best-effort backend diagnostics for the most recent XY move.

        Backends that can expose raw command/read values should override this.
        Callers must treat missing keys as unavailable data.
        """
        return {}

    # -- nm convenience wrappers (single conversion; never chain them) ----

    def move_relative(self, dx_nm: float, dy_nm: float) -> None:
        """Move by (dx_nm, dy_nm) nanometres — converted once to pulses."""
        k = self.pulses_per_nm()
        self.move_relative_pulses(dx_nm * k, dy_nm * k)

    def read_xy_nm(self) -> Tuple[float, float]:
        """Return current galvo position (x, y) in nm relative to center."""
        k = self.pulses_per_nm()
        x_p, y_p = self.read_xy_pulses()
        return (x_p / k, y_p / k)

    def set_home(self, x_nm: float | None = None, y_nm: float | None = None) -> Tuple[float, float]:
        """nm counterpart of ``set_home_pulses``."""
        k = self.pulses_per_nm()
        if x_nm is None or y_nm is None:
            x_p, y_p = self.set_home_pulses()
        else:
            x_p, y_p = self.set_home_pulses(x_nm * k, y_nm * k)
        return (x_p / k, y_p / k)


class NeaBackend(ABC):
    """Abstract interface for the neaSNOM **Z** axis and optical signal readout."""

    @abstractmethod
    def connect(self, host: str = "nea-server") -> None:
        """Open the neaSNOM connection.

        *host*: hostname / IP of the neaSNOM server (ignored by mock).
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Release the neaSNOM connection."""
        ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def move_z_relative(self, dz_nm: float) -> None:
        """Move the parabolic mirror along Z by *dz_nm* from its current position."""
        ...

    @abstractmethod
    def read_z_nm(self) -> float:
        """Return current mirror Z position in nm relative to its startup reference."""
        ...

    @abstractmethod
    def available_z_steps_nm(self) -> tuple[float, ...]:
        """Return the supported Z jog step sizes from Z_STEP_OPTIONS_NM."""
        ...

    @abstractmethod
    def read_sample(
        self,
        t_integ_s: float = 0.05,
        xy_nm: Tuple[float, float] = (0.0, 0.0),
        xy_pulses: Tuple[float, float] = (0.0, 0.0),
    ) -> SnomSample:
        """Read one snapshot of neaSNOM optical signals.

        *t_integ_s*: signal integration window in seconds (stream averaging).
        *xy_nm*, *xy_pulses*: galvo position to tag the sample with (supplied
        by the caller, which owns the galvo backend).
        """
        ...


def run_raster_scan(
    galvo: GalvoBackend,
    nea: NeaBackend,
    dx_nm: float,
    dy_nm: float,
    nb_x: int,
    nb_y: int,
    twait: float,
    t_integ_s: float,
    on_point: Callable[[int, int, SnomSample], None],
    stop_check: Callable[[], bool],
    *,
    settle: Callable[[float], None] | None = None,
) -> None:
    """Run a 2-D raster scan centred on the current galvo position.

    Moves in a left-to-right raster pattern (like the lab notebooks): starts at
    (-dx/2, -dy/2), steps right across each row, then steps down one row and
    returns to the left edge.  Reads the neaSNOM optical signal at each pixel
    and returns to centre when done (or when *stop_check* returns True).

    Args:
        galvo:      connected galvo backend (XY motion)
        nea:        connected neaSNOM backend (optical readout)
        dx_nm:      total scan range in x (nm)
        dy_nm:      total scan range in y (nm)
        nb_x:       pixels in x
        nb_y:       pixels in y
        twait:      sleep time (s) after each move before reading
        t_integ_s:  signal integration window (s) per pixel
        on_point:   callback(ix, iy, sample) called after each pixel read
        stop_check: callable returning True to request early stop
        settle:     sleep hook (defaults to time.sleep); injectable for tests
    """
    if settle is None:
        import time

        settle = time.sleep

    step_x = dx_nm / (nb_x - 1) if nb_x > 1 else 0.0
    step_y = dy_nm / (nb_y - 1) if nb_y > 1 else 0.0

    # Move to start corner (-dx/2, -dy/2) relative to current centre.
    k = galvo.pulses_per_nm()
    galvo.move_relative(-dx_nm / 2.0, -dy_nm / 2.0)
    settle(twait)
    x_start, _y_start = galvo.read_xy_nm()  # actual position after quantisation

    for iy in range(nb_y):
        for ix in range(nb_x):
            if stop_check():
                break

            xy_p = galvo.read_xy_pulses()  # single hardware read; nm derived from it
            xy = (xy_p[0] / k, xy_p[1] / k)
            sample = nea.read_sample(t_integ_s, xy, xy_p)
            on_point(ix, iy, sample)

            if ix < nb_x - 1:
                galvo.move_relative(step_x, 0.0)
                settle(twait)

        if stop_check():
            break

        if iy < nb_y - 1:
            # End of row: correct back to x_start, step y down.
            x_curr, _ = galvo.read_xy_nm()
            galvo.move_relative(x_start - x_curr, step_y)
            settle(twait)

    # Return to centre.
    galvo.goto_center()

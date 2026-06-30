"""Atomic HDF5 save for galvo scan results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import h5py
import numpy as np

_N_HARMONICS = 6


def save_scan_h5(
    path: str | Path,
    amp: np.ndarray,
    phase: np.ndarray,
    coords: np.ndarray,
    metadata: Dict[str, Any],
) -> None:
    """Write galvo scan to HDF5 atomically (writes .tmp then renames).

    Datasets written:
        O0 .. O5  : complex64 (ny, nx) — amp * exp(1j * phase), matches notebook convention
        coordinates: float64 (ny, nx, 2) — actual galvo readback (x, y) in nm
        amp_O0 .. amp_O5  : float64 (ny, nx) — raw amplitude (convenience)
        phase_O0 .. phase_O5: float64 (ny, nx) — raw phase in radians (convenience)

    Attributes:
        metadata: JSON string of scan parameters and timestamp.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")

    try:
        with h5py.File(tmp_path, "w") as h5:
            h5.attrs["metadata"] = json.dumps(metadata, default=str)

            for h in range(_N_HARMONICS):
                a = amp[h].astype(np.float64)
                p = phase[h].astype(np.float64)
                # Complex dataset matches notebook: O{h} = amp * exp(1j * phase)
                h5.create_dataset(f"O{h}", data=(a * np.exp(1j * p)).astype(np.complex64))
                # Raw channels for easy inspection without trigonometry
                h5.create_dataset(f"amp_O{h}", data=a)
                h5.create_dataset(f"phase_O{h}", data=p)

            h5.create_dataset("coordinates", data=coords.astype(np.float64))

        # Atomic rename
        if os.name == "nt" and tmp_path.exists() and path.exists():
            # os.replace on Windows fails if dst exists (edge case on retries)
            path.unlink()
        tmp_path.replace(path)
    except Exception:
        import contextlib
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise

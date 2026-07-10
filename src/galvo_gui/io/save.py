"""Atomic save helpers for galvo scan results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import h5py
import numpy as np

_N_HARMONICS = 6


def _write_text_scan(
    path: Path,
    amp: np.ndarray,
    phase: np.ndarray,
    coords_pulses: np.ndarray,
    metadata: Dict[str, Any],
    coords_z: np.ndarray | None = None,
) -> None:
    """Write a companion text export with metadata header and one row per pixel.

    2-D scans (default): one row per (row, col) pixel.
    3-D scans (*coords_z* given): adds a leading #slice column and z_nm.
    """
    tmp_path = path.parent / (path.name + ".tmp")
    is_3d = coords_z is not None

    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            for key, value in metadata.items():
                if isinstance(value, dict):
                    fh.write(f"# {key}:\n")
                    for sub_key, sub_value in value.items():
                        fh.write(f"#   {sub_key}: {sub_value}\n")
                else:
                    fh.write(f"# {key}: {value}\n")

            header = ["#slice", "z_nm"] if is_3d else []
            header += ["#row", "#col", "x_pulse", "y_pulse"]
            for h in range(_N_HARMONICS):
                header.extend((f"O{h}A", f"O{h}P"))
            fh.write("# " + " - ".join(header) + "\n")

            if is_3d:
                nz, ny, nx = coords_pulses.shape[:3]
            else:
                nz, ny, nx = 1, *coords_pulses.shape[:2]

            for iz in range(nz):
                amp_z = amp[:, iz] if is_3d else amp
                phase_z = phase[:, iz] if is_3d else phase
                coords_z_p = coords_pulses[iz] if is_3d else coords_pulses
                for iy in range(ny):
                    for ix in range(nx):
                        row = [str(iz), f"{float(coords_z[iz]):.10g}"] if is_3d else []
                        row += [
                            str(iy),
                            str(ix),
                            f"{float(coords_z_p[iy, ix, 0]):.10g}",
                            f"{float(coords_z_p[iy, ix, 1]):.10g}",
                        ]
                        for h in range(_N_HARMONICS):
                            row.extend(
                                (
                                    f"{float(amp_z[h, iy, ix]):.10g}",
                                    f"{float(phase_z[h, iy, ix]):.10g}",
                                )
                            )
                        fh.write(" - ".join(row) + "\n")

        if os.name == "nt" and tmp_path.exists() and path.exists():
            path.unlink()
        tmp_path.replace(path)
    except Exception:
        import contextlib
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def save_scan_h5(
    path: str | Path,
    amp: np.ndarray,
    phase: np.ndarray,
    coords: np.ndarray,
    coords_pulses: np.ndarray,
    metadata: Dict[str, Any],
    coords_z: np.ndarray | None = None,
) -> None:
    """Write galvo scan to HDF5 atomically (writes .tmp then renames).

    2-D scan (default, *coords_z* omitted) datasets:
        O0 .. O5  : complex64 (ny, nx) — amp * exp(1j * phase), matches notebook convention
        coordinates: float64 (ny, nx, 2) — actual galvo readback (x, y) in nm
        coordinates_pulses: float64 (ny, nx, 2) — actual galvo readback (x, y) in encoder pulses
        amp_O0 .. amp_O5  : float64 (ny, nx) — raw amplitude (convenience)
        phase_O0 .. phase_O5: float64 (ny, nx) — raw phase in radians (convenience)

    3-D scan (*coords_z* given, a Z stack): the same datasets gain a leading
    Z axis — (nz, ny, nx) / (nz, ny, nx, 2) — plus:
        coordinates_z: float64 (nz,) — actual neaSNOM Z readback (nm) per slice

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
            h5.create_dataset("coordinates_pulses", data=coords_pulses.astype(np.float64))
            if coords_z is not None:
                h5.create_dataset("coordinates_z", data=coords_z.astype(np.float64))

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


def save_scan_text(
    path: str | Path,
    amp: np.ndarray,
    phase: np.ndarray,
    coords_pulses: np.ndarray,
    metadata: Dict[str, Any],
    coords_z: np.ndarray | None = None,
) -> None:
    """Write a companion text export atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_scan(path, amp, phase, coords_pulses, metadata, coords_z=coords_z)

# Galvo Motor Control GUI

Desktop app for manual jogging and 2-D raster scanning with a galvo laser mirror on a **neaSNOM** near-field optical microscope. Replaces the original Jupyter notebooks with a live GUI, persistent settings, and atomic HDF5 output.

## Features

- **Manual tab** — cross controller (▲▼◀▶) with configurable step size (nm), live X/Y position readout, go-to-centre button
- **Scan tab** — configurable 2-D raster scan (size, pixels, step time, integration time), live amplitude/phase image updated pixel-by-pixel, HDF5 save
- **Mock backend** — runs without any hardware for development and testing
- **Real backend** — wraps `galvo_functions.Galvo` for galvo control and `nea_tools` / `neaspec` for SNOM optical signal readout
- Dark research-workstation UI matching [Andor_idus420_Demodulation_gui](https://github.com/your-lab/Andor_idus420_Demodulation_gui)

## Requirements

- **Python ≥ 3.8**
- `PyQt6 ≥ 6.4`, `pyqtgraph ≥ 0.13`, `numpy`, `h5py` (installed automatically)

**For real hardware** (lab PC only):
- `galvo_functions.py` on `sys.path` (local lab script, not on PyPI)
- `nea_tools` + `nest_asyncio` — see `[snom]` extra below

## Installation

```bash
# Clone and install (development mode)
git clone https://github.com/your-lab/galvo-gui.git
cd galvo-gui

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"           # GUI + tests (no hardware)
pip install -e ".[dev,snom]"      # + nea_tools SNOM support
```

## Running

```bash
# From repo root
PYTHONPATH=src python -m galvo_gui

# Or after pip install -e .
galvo-gui
```

**Windows (lab PC):** double-click `launch_gui.vbs` — launches into the `py38` conda environment with no console window.

## Tabs

### Manual
Select **Backend** (Mock or Real), enter the **neaSNOM host** and **cal files path**, then click **Connect**. Use the cross controller to jog the galvo; the position readout refreshes at 2 Hz.

### Scan
Set scan range (nm), pixel counts, step time, and save location. Click **Start Scan** — the image fills live from left-to-right rasters. Each pixel records 6 optical harmonics (amplitude + phase) via the neaSNOM stream. Results are saved as HDF5 on completion.

## HDF5 output format

```
/O0 … O5          complex64 (ny, nx)   amp * exp(1j * phase)  ← matches notebooks
/amp_O0 … amp_O5  float64   (ny, nx)   raw amplitude
/phase_O0…O5      float64   (ny, nx)   raw phase (radians)
/coordinates       float64   (ny, nx, 2)  actual galvo readback (x, y) in nm
attrs: metadata    JSON string (scan params, timestamp)
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m pytest -q
```

All tests run without hardware using the mock backend. Qt tests require `pytest-qt`.

## Notes

- `galvo_functions.Move(dx, dy)` is a **relative** move; the scan always re-centres the galvo after completing.
- Phase readout requires `nea_tools.microscope.stream` (available only after `nea_tools.connect`).
- The `cal_files/` directory path (used by `galvo_functions.Galvo`) is configurable in the Connection group and persisted via `QSettings`.

## License

MIT — see [LICENSE](LICENSE).

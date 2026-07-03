# Galvo Motor Control GUI

Desktop app for manual jogging and 2-D raster scanning with a galvo laser mirror on a **neaSNOM** near-field optical microscope. Replaces the original Jupyter notebooks with a live GUI, persistent settings, and atomic HDF5 output.

## Features

- **Manual tab** — cross controller (▲▼◀▶) with configurable step size, live X/Y position readout, go-to-centre button. The galvo is driven in its native **encoder pulses** (with an optional nm read-out), so a move never round-trips through nm
- **Scan tab** — configurable 2-D raster scan (size, pixels, step time, integration time), live amplitude/phase image updated pixel-by-pixel, HDF5 save
- **Two independent connections** — the **neaSNOM** (parabolic-mirror Z axis + optical signal) and the **galvomotor** (XY stage) connect and disconnect separately; each enables only its own motion controls
- **Galvo driver modes** — *Simulated galvo* (no hardware), *GB511 board* (`galvo_functions.Galvo`), or *Canon GC-211/212* (GB511 + RS-232 high-speed)
- **neaSNOM** — wraps `nea_tools` / `neaspec` for Z motion and optical signal readout (falls back to a simulated neaSNOM when the SDK is absent)
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

### Connection
Two separate connections sit side by side. **neaSNOM** takes a host and connects the Z axis + optical signal. **Galvomotor** takes a driver mode (Simulated / GB511 / Canon) plus its cal-files path (and, for Canon, serial port / board index / program file). Each has its own **Connect / Disconnect** button.

### Motion
The **Galvomotor XY** cross controller (▲▼◀▶) is enabled only while the galvo is connected; the **neaSNOM Z** controls only while neaSNOM is connected. Live readouts refresh at 2 Hz. XY steps, targets, and readouts are in **encoder pulses** (the galvo's native unit); a units toggle can show positions in nm for reference, but commands are always issued in pulses — no `pulse → nm → pulse` conversion.

### Scan
A scan needs **both** connections (galvo for XY motion, neaSNOM for optical readout). Set scan range (nm), pixel counts, step time, and save location, then click **Start Scan** — the image fills live from left-to-right rasters. Each pixel records 6 optical harmonics (amplitude + phase) via the neaSNOM stream. Results are saved as HDF5 on completion.

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

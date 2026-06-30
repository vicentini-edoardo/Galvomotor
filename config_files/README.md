Local hardware bundle for the real galvo backend.

Keep these files here on the lab PC:

- `galvo_functions.py`
- `CanonGB511.dll`
- `gbdsp.hex`
- `GM-2020-ftheta-10mm-fo4.tsc`
- `cal_files/` with at least one `*-galvocal.txt`

This directory is gitignored on purpose. It may contain vendor binaries,
machine-specific calibration, and scratch calibration artifacts that should not
be pushed to a public remote by default.

Local hardware bundle for the real galvo backend.

## Keep These Files Local

Keep these files here on the lab PC:

- `galvo_functions.py`
- `CanonGB511.dll`
- `gb501p.dll`
- `gbdsp.hex`
- `GM-2020-ftheta-10mm-fo4.tsc`
- `cal_files/` with at least one `*-galvocal.txt`

This directory is gitignored on purpose. It may contain vendor binaries,
machine-specific calibration, firmware/program files, and scratch calibration
artifacts that should not be pushed to a public remote by default.

## Files That Are Not Safe For A Public GitHub Repo

These files should not be tracked in a public repository:

- `CanonGB511.dll` and `gb501p.dll`
  Vendor DLLs are usually not safe to redistribute publicly.
- `gbdsp.hex`
  Hardware program/firmware asset for the controller.
- `GM-2020-ftheta-10mm-fo4.tsc`
  Correction/calibration data tied to the hardware setup.
- `galvo_functions.py`
  Local lab integration script, not a normal public dependency.
- Old lab notebooks containing saved outputs, local paths, or machine metadata
  should also stay out of the public repo when possible.

## Why The App Still Works If These Files Are Removed From Git

The application already loads the real-hardware bundle from `config_files/`,
not from `notebooks/`.

- `src/galvo_gui/motion/galvo_nea.py` adds `config_files/` to `sys.path`
  so `galvo_functions.py` can be imported from here.
- `src/galvo_gui/motion/canon/gb511.py` defaults to
  `config_files/CanonGB511.dll` and `config_files/gbdsp.hex`.
- The GUI defaults the calibration path to `config_files/cal_files`.

That means the correct setup is:

- keep the hardware bundle locally inside `config_files/`
- keep it ignored by git
- do not track duplicate copies under `notebooks/`

## How To Remove These Files From GitHub But Keep Them Locally

1. Move the local-only files into `config_files/` if they are still sitting in
   `notebooks/`.

   Example:

   ```bash
   mv notebooks/CanonGB511.dll config_files/
   mv notebooks/gb501p.dll config_files/
   mv notebooks/gbdsp.hex config_files/
   mv notebooks/GM-2020-ftheta-10mm-fo4.tsc config_files/
   mv notebooks/galvo_functions.py config_files/
   ```

2. Stop tracking the public-risk files without deleting your local copies.

   ```bash
   git rm --cached notebooks/CanonGB511.dll
   git rm --cached notebooks/gb501p.dll
   git rm --cached notebooks/gbdsp.hex
   git rm --cached notebooks/GM-2020-ftheta-10mm-fo4.tsc
   git rm --cached notebooks/galvo_functions.py
   git rm --cached "notebooks/260220 - Galvo-Parabolic-snom-scan.ipynb"
   ```

3. Add matching ignore rules in the top-level `.gitignore` so those notebook
   copies do not get re-added later.

4. Commit and push the cleanup.

`git rm --cached` removes files from the repository index, but leaves the local
files in place on disk.

## Important Note About Git History

If any of these files were already pushed to GitHub, removing them in a new
commit only removes them from the latest revision. They still remain in the
repository history until that history is rewritten.

Typical history rewrite flow:

```bash
git filter-repo \
  --path notebooks/CanonGB511.dll \
  --path notebooks/gb501p.dll \
  --path notebooks/gbdsp.hex \
  --path notebooks/GM-2020-ftheta-10mm-fo4.tsc \
  --path notebooks/galvo_functions.py \
  --path "notebooks/260220 - Galvo-Parabolic-snom-scan.ipynb" \
  --invert-paths
git push --force --all
git push --force --tags
```

Rewriting history changes commit hashes, so any collaborators must resync their
clones afterward.

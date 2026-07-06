# Galvo Tolerance Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a connection-page `Tolerance (pulses)` field that persists, flows into real galvo backends, and controls the XY `GalvoError` follow-check threshold.

**Architecture:** Keep the setting in the existing galvo connection section and pass it into the real backend constructors at connect time. Store the configured pulse tolerance on `RealGalvoBackend` and have XY follow validation read that instance value instead of relying on the current fixed helper result.

**Tech Stack:** PyQt6, pytest, existing galvo backend classes in `src/galvo_gui/motion`.

---

### Task 1: Lock the requested behavior with tests

**Files:**
- Modify: `tests/test_motion_ui.py`
- Modify: `tests/test_config_bundle.py`

- [ ] **Step 1: Write the failing UI test for persistence and constructor wiring**

Add a test that sets the new galvo connection tolerance field, saves settings, restores the section, and verifies the restored value. Add a backend-construction test that monkeypatches the real or Canon backend class and asserts the captured init kwargs include `axis_follow_tolerance_pulses`.

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_motion_ui.py -k tolerance -v`
Expected: FAIL because the connection section has no tolerance field and does not pass the constructor argument yet.

- [ ] **Step 3: Write the failing backend tolerance test**

Add a test that sets a custom backend tolerance and verifies `_validate_axis_follow()` accepts a miss inside tolerance and raises on a miss above tolerance.

- [ ] **Step 4: Run the targeted backend test to verify it fails**

Run: `pytest tests/test_config_bundle.py -k tolerance -v`
Expected: FAIL because the backend does not yet expose or use an instance-configured tolerance.

### Task 2: Add the connection-page field and settings plumbing

**Files:**
- Modify: `src/galvo_gui/gui/panel_manual.py`
- Test: `tests/test_motion_ui.py`

- [ ] **Step 1: Add the `Tolerance (pulses)` UI field**

Place a positive-integer input in `GalvoConnectionSection._build_fields`, shift the Canon-only rows down, and include the new row in the existing real-hardware visibility group.

- [ ] **Step 2: Persist and restore the new field**

Update `GalvoConnectionSection._restore_settings()` and `save_settings()` to use a new QSettings key with default value `5`.

- [ ] **Step 3: Parse the tolerance when building real galvo backends**

Add a small helper in `GalvoConnectionSection` that returns a validated integer pulse tolerance and use it in both the GB511 and Canon backend construction paths.

- [ ] **Step 4: Run the targeted UI tests**

Run: `pytest tests/test_motion_ui.py -k "tolerance or canon_settings" -v`
Expected: PASS

### Task 3: Make the backend use the configured tolerance

**Files:**
- Modify: `src/galvo_gui/motion/galvo_nea.py`
- Modify: `src/galvo_gui/motion/canon/backend.py`
- Test: `tests/test_config_bundle.py`

- [ ] **Step 1: Add the backend constructor parameter**

Extend `RealGalvoBackend.__init__` with `axis_follow_tolerance_pulses: int = 5`, clamp it to at least `1`, and store it on the instance. Thread the same kwarg through `CanonGalvoBackend.__init__`.

- [ ] **Step 2: Use the instance tolerance in XY follow validation**

Update `_validate_axis_follow()` to read the configured pulse tolerance for both axes, keeping the existing error messages unchanged.

- [ ] **Step 3: Leave the helper only as the default behavior source if still useful**

Either keep `_axis_follow_tolerance_pulses()` for the default calculation/reference or remove dead use if no longer needed. Do not add a setter or reconnect-free runtime path.

- [ ] **Step 4: Run the targeted backend tests**

Run: `pytest tests/test_config_bundle.py -k tolerance -v`
Expected: PASS

### Task 4: Final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-06-galvo-tolerance-connection-design.md`
- Modify: `docs/superpowers/plans/2026-07-06-galvo-tolerance-connection.md`

- [ ] **Step 1: Run the focused regression suite**

Run: `pytest tests/test_motion_ui.py tests/test_config_bundle.py -v`
Expected: PASS

- [ ] **Step 2: Sanity-check the diff for scope**

Run: `git diff -- src/galvo_gui/gui/panel_manual.py src/galvo_gui/motion/galvo_nea.py src/galvo_gui/motion/canon/backend.py tests/test_motion_ui.py tests/test_config_bundle.py docs/superpowers/specs/2026-07-06-galvo-tolerance-connection-design.md docs/superpowers/plans/2026-07-06-galvo-tolerance-connection.md`
Expected: only the new tolerance field, plumbing, backend use, tests, and docs.

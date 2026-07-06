# Galvo Tolerance Connection Design

## Goal

Add a dedicated galvo connection setting for XY follow tolerance, expressed only in raw pulses, so operators can adjust the `GalvoError` threshold without editing code.

## Scope

This change applies only to the galvo XY follow check that compares commanded pulses against hardware read-back after a move. It does not change Z motion, motion-tab unit display, or scan parameter units.

## UI

Add a `Tolerance (pulses)` field to the Galvo connection section on the Connection page. The field belongs with the other galvo connection parameters and stays in raw pulses regardless of any Motion tab display toggle.

For simulated mode the field may be hidden along with the other real-hardware-only galvo settings. For real galvo modes it should be visible, persisted in `QSettings`, and reused on the next launch.

## Backend

Thread the configured tolerance into `RealGalvoBackend` construction and store it as an instance value. The XY follow validation should use that configured pulse tolerance instead of the current hard-coded helper result.

Canon should inherit the same behavior by passing the same constructor argument through to `RealGalvoBackend`.

## Defaults and Validation

Default tolerance is `5` pulses to preserve current behavior. The UI should only accept positive integer pulse values, and backend construction should clamp invalid or too-small values back to at least `1`.

## Tests

Add one UI regression test to prove the connection section persists and passes the configured tolerance into backend construction. Add one backend regression test to prove a custom tolerance changes whether `_validate_axis_follow()` raises `GalvoError`.

# Canon GC-211/212 Galvo Controller Design

Date: 2026-06-30
Status: approved in chat, awaiting final user review of this file

## Goal

Add a Canon GC-211/212 + GM-1000 real backend to the existing `galvo_gui` app.

The backend must manage two live hardware channels at the same time:

- RS-232 for management, status, homing, servo control, error handling, parameter access, and operation mode switching.
- High-speed serial motion through the GB511 board for real-time target position streaming.

The GUI should continue talking to a single backend object through the existing `GalvoBackend` interface.

## Scope

In scope:

- Add a Canon-specific backend implementation under the existing motion layer.
- Wrap the Canon RS-232 ASCII protocol in a high-level Python class.
- Wrap `CanonGB511.dll` in a Python motion-controller class.
- Enforce the documented startup and shutdown sequence in one backend entry point.
- Add GUI connection options for Canon hardware with auto-detect plus manual serial-port override.
- Keep both RS-232 and GB511 connections open for the full backend lifetime.

Out of scope for this change:

- Any fake optical/sample data for Canon scans.
- Enabling the existing scan-image workflow for Canon without a real signal source.
- A broad refactor of the GUI or backend contracts unrelated to Canon support.

## Existing Context

The repo already has the right seam for this work:

- `src/galvo_gui/motion/base.py` defines `GalvoBackend`.
- `src/galvo_gui/gui/panel_manual.py` owns backend selection and connection lifecycle.
- `src/galvo_gui/workers/scan.py` and the scan panel already lock the UI around backend-driven motion.
- `config_files/CanonGB511.dll` is already bundled locally for the real backend workflow.
- The Canon manual in `notebooks/GM-1000+GC-21x_Manual_EN.pdf` defines the RS-232 framing and command IDs.

This means the smallest useful design is to add one more real backend instead of rewriting the app architecture.

## Recommended Approach

Keep the current `GalvoBackend` shape and implement Canon support by composition:

- `CanonRS232` owns the low-bandwidth ASCII management channel.
- `GB511MotionController` owns the high-speed trajectory path via `CanonGB511.dll`.
- `CanonGalvoBackend` composes both and implements the existing backend API used by the GUI.

This avoids pushing hardware-specific sequencing into the GUI and keeps the diff localized to the motion layer plus a small connection-panel extension.

## Package Layout

Add a Canon package under `src/galvo_gui/motion/canon/`:

- `__init__.py`
- `rs232.py`
- `gb511.py`
- `backend.py`

Optional helper placement:

- Keep protocol constants in `rs232.py` unless reuse makes a separate constants module clearly smaller.

No extra abstraction layer is planned beyond these three concrete classes.

## Class Design

### `CanonRS232`

Purpose:

- Hide the vendor ASCII protocol.
- Expose only high-level management and monitoring methods.

Connection details from the manual:

- Cross-wired RS-232
- `38400` baud
- `8N1`
- LF delimiter
- ASCII framing like `A1C004/1\n`

Public methods:

- `connect(port: str | None = None, timeout_s: float = ...)`
- `disconnect()`
- `servo_on(axis: int)`
- `servo_off(axis: int)`
- `home(axis: int)`
- `clear_error(axis: int)`
- `read_status(axis: int) -> CanonStatus`
- `read_position(axis: int, target_mode: int = 0) -> int`
- `read_temperature(axis: int, source: int) -> float`
- `read_errors(axis: int) -> CanonErrors`
- `read_version(axis: int, source: int) -> int`
- `switch_high_speed(axis: int)`
- `switch_rs232(axis: int)`
- `read_parameter(axis: int, parameter_id: int) -> int`
- `write_parameter(axis: int, parameter_id: int, value: int)`

Implementation notes:

- Command IDs come from the Canon manual.
- `switch_high_speed()` sends command `23` with data `7`.
- `switch_rs232()` sends command `23` with data `0`.
- Replies should be validated against the requested axis and command ID.
- The class should raise `GalvoError` on timeout, malformed reply, or command failure.
- Auto-detect will scan candidate serial ports, but manual port override must be available in the GUI.

### `GB511MotionController`

Purpose:

- Own motion hardware initialization and shutdown.
- Feed trajectories/positions to the GB511 board through `CanonGB511.dll`.

Known DLL surface from exports/strings:

- Board lifecycle: `gb511_open`, `gb511_close`
- Motion/control functions such as `ctr_reset_param`, `ctr_load_program_file`, `ctr_get_current_xy_pos`, `ctr_goto_xy`, `ctr_servo_off`, `ctr_reset_and_detect_origin`, `ctr_clear_alarm`, `ctr_get_current_thermo`

Public methods:

- `initialize(board_index: int = 0, program_file: str | None = None)`
- `shutdown()`
- `start()`
- `stop()`
- `load_waveform(...)`
- `update_positions(x_bits: int, y_bits: int)`
- `read_current_xy_bits() -> tuple[int, int]`
- `read_target_xy_bits() -> tuple[int, int]`

Implementation notes:

- Use `ctypes.WinDLL` and declare only the functions actually used.
- Keep the wrapper narrow. Do not mirror the full DLL API.
- Treat GB511 as the real-time path only; configuration/monitoring stays on RS-232.
- The backend may use GB511 for raw XY motion commands and/or continuous streaming depending on what the current DLL/program file path supports.

### `CanonGalvoBackend`

Purpose:

- Present a single backend to the GUI.
- Manage both connections for the full application session.
- Enforce the Canon startup and shutdown sequence.

This class implements the existing `GalvoBackend` interface where meaningful.

Behavior mapping:

- `connect(...)` initializes GB511, opens RS-232, clears alarms, enables servo, homes if needed, starts motion streaming, then switches both axes to high-speed mode.
- `disconnect()` stops motion, switches both axes back to RS-232 mode, turns servo off, closes RS-232, then shuts down GB511.
- `read_xy_nm()` should return current position from hardware readback, using the most reliable GB511 or Canon read function available.
- `move_relative()` and `goto_center()` should only be allowed when operating in RS-232/manual mode, or the backend should switch temporarily and restore mode if that proves necessary and safe.
- `scan()` is explicitly unavailable for the Canon backend in this phase because there is no approved real signal-readout source yet.

## Startup Sequence

`CanonGalvoBackend.connect()` must perform this order:

1. `GB511MotionController.initialize()`
2. `CanonRS232.connect()`
3. `clear_error(1)`
4. `clear_error(2)`
5. `servo_on(1)`
6. `servo_on(2)`
7. Read `Status Read` for each axis
8. If an axis is not synced, `home(axis)`
9. Start GB511 high-speed signal generation
10. `switch_high_speed(1)`
11. `switch_high_speed(2)`

Notes:

- The GB511 side must already be producing valid `CLK`, `FS`, and `DATA` before switching the driver to high-speed mode.
- RS-232 stays connected after the switch and remains usable for monitoring.

## Shutdown Sequence

`CanonGalvoBackend.disconnect()` must perform this order:

1. Stop motion streaming
2. `switch_rs232(1)`
3. `switch_rs232(2)`
4. `servo_off(1)`
5. `servo_off(2)`
6. Close RS-232
7. Shutdown GB511

If shutdown is triggered after a partial startup failure, the backend should best-effort unwind whatever was already opened.

## GUI Changes

Extend the existing connection panel rather than adding a new window.

Add a backend option:

- `Canon (GC-211/212 + GB511)`

Add Canon-specific connection fields:

- Serial port auto-detect status
- Manual serial port override
- Optional board index
- Optional path/program file field if GB511 requires a DSP/hex/program artifact at runtime

Behavior:

- Auto-detect first, then fall back to the manual override if present.
- Persist these settings with the existing `QSettings` pattern.
- Reuse the existing connect/disconnect lifecycle in `ConnectionPanel`.

## Scanning Behavior

The Canon backend will not support the existing scan-image workflow in this change.

Decision:

- Do not fabricate optical data.
- Do not emit dummy samples.
- Do not enable `scan()` until a real signal source is integrated and designed.

Expected backend behavior:

- `scan()` raises `GalvoError` with a clear message saying Canon motion is available but scan imaging is disabled until a signal-readout source is added.

This is deliberate to keep the motion integration honest and avoid silently producing meaningless data.

## Error Handling

Rules:

- Translate transport errors, DLL call failures, and command failures into `GalvoError`.
- Decode status and error bitfields into readable structures for logs and diagnostics.
- Fail fast on invalid RS-232 replies.
- If switching to high-speed mode fails on either axis, revert both axes to RS-232 mode and surface the failure.
- If homing fails, abort connect and unwind opened resources.

Monitoring during operation:

- RS-232 remains available for status, temperature, position, errors, and version reads while high-speed streaming is active.

## Tests

Follow TDD for the implementation phase.

Minimum tests to add:

- RS-232 command formatting and reply parsing from the Canon ASCII protocol.
- Status and error bit decoding.
- Startup sequence ordering in `CanonGalvoBackend`.
- Shutdown sequence ordering in `CanonGalvoBackend`.
- Partial-startup unwind behavior.
- Connection-panel persistence for the Canon serial-port and board settings.
- Canon backend `scan()` rejection with a clear error message.

Use fakes/mocks for:

- Serial transport
- DLL wrapper calls

Do not require real hardware in automated tests.

## Non-Goals

- Do not add a second GUI architecture.
- Do not build a generalized hardware-plugin system.
- Do not expose low-level ASCII commands directly to the GUI.
- Do not add fake scan data just to satisfy the existing scan panel.

## Open Implementation Questions

These are implementation questions, not product-scope questions:

- Which exact GB511 program/hex file must be loaded at startup in this repo’s environment.
- Which exported GB511 functions are sufficient for continuous high-speed position streaming in the current lab setup.
- Whether manual jog actions should temporarily switch to RS-232 mode or use GB511 direct motion calls while keeping the driver in high-speed mode.

The implementation should answer these by reading the local lab wrapper/assets first and choose the smallest working path.

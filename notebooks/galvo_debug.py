"""Standalone GB511 galvo motion debugger for the lab PC.

Steps through every layer between Python and the mirrors and prints the
return code of every DLL call, so the dead layer is identified precisely:

  0. Required files present        (CanonGB511.dll, gb501p.dll, gbdsp.hex, .tsc)
  1. DLL load                      (32/64-bit and dependency problems surface here)
  2. DSP program + correction file (ctr_load_program_file / ctr_load_correction_file)
  3. Board selection               (ctr_select_board — USB/LAN/PCIe, board index)
  4. Status, jump speed, read-back (ctr_read_status / ctr_get_current_xy_pos / ctr_get_xy_pos)
  5. Motion response sweep         (absolute ctr_goto_xy targets, read-back after each,
                                    measures the real read-pulses-per-goto-unit ratio)
  6. Optional list-mode move test  (--list-test: lst_jump_abs + ctr_execute_list)
  7. Optional GC-211/212 RS-232 servo bring-up (--rs232 COM3: clear errors,
                                    servo ON, home if needed, switch to high-speed)

IMPORTANT before running:
  - CLOSE the Canon "Galvano Scanner Control Software": the board is
    single-client; while it is connected this script (and the GUI) talk
    to nothing.
  - The GC-211/212 drivers must have servo ON and be in HIGH-SPEED SERIAL
    mode to follow the GB511 board. Do that in the Canon software (Servo ON
    + HiSpeed Serial Start, then close it) or pass --rs232 COMx to let this
    script do it over RS-232.

Usage (Windows lab PC):

    python galvo_debug.py                          # auto-detect the DLL folder
    python galvo_debug.py --dir C:\\path\\to\\config_files
    python galvo_debug.py --scan-board             # try all port/board indices
    python galvo_debug.py --rs232 COM3             # servo bring-up first
    python galvo_debug.py --sweep 0 1000 -1000 5000 -5000 0
    python galvo_debug.py --list-test

Everything is also written to galvo_debug_YYYYMMDD-HHMMSS.txt in the
current directory — send that file back for analysis.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

REQUIRED_FILES = ("CanonGB511.dll", "gbdsp.hex", "GM-2020-ftheta-10mm-fo4.tsc")
DEPENDENCY_FILES = ("gb501p.dll",)  # loaded implicitly by CanonGB511.dll

DSP_FILE = "gbdsp.hex"
CORRECTION_FILE = "GM-2020-ftheta-10mm-fo4.tsc"

# From the working notebook's galvo_move(): goto units per read pulse.
CX_NOMINAL = 0.11080

# -524288 == -2**19: value both axes report when the read-back is not live
# (board not selected / another client holds it) or an axis is railed.
SENTINEL = -(2**19)

DEFAULT_SWEEP = [0, 500, -500, 2000, -2000, 5000, -5000, 0]


class Log:
    def __init__(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = Path.cwd() / f"galvo_debug_{stamp}.txt"
        self._fh = open(self.path, "w", encoding="utf-8")  # noqa: SIM115 — lives for the whole run

    def line(self, msg: str = "") -> None:
        print(msg)
        self._fh.write(msg + "\n")
        self._fh.flush()

    def section(self, title: str) -> None:
        self.line()
        self.line("=" * 64)
        self.line(title)
        self.line("=" * 64)


# ----------------------------------------------------------------------
# Step 0/1: files and DLL
# ----------------------------------------------------------------------

def find_dll_dir(explicit: str | None, log: Log) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    here = Path(__file__).resolve().parent
    candidates += [
        Path.cwd(),
        Path.cwd() / "galvomotor",
        here,
        here / "galvomotor",
        here.parent / "config_files",
    ]
    for cand in candidates:
        if (cand / "CanonGB511.dll").exists():
            return cand.resolve()
    log.line("FAIL: CanonGB511.dll not found. Looked in:")
    for cand in candidates:
        log.line(f"  - {cand}")
    log.line("Pass the folder explicitly:  python galvo_debug.py --dir <folder>")
    sys.exit(1)


def check_files(dll_dir: Path, log: Log) -> None:
    log.section("STEP 0 — required files")
    log.line(f"DLL folder: {dll_dir}")
    ok = True
    for name in REQUIRED_FILES:
        exists = (dll_dir / name).exists()
        log.line(f"  {'OK  ' if exists else 'MISS'}  {name}")
        ok &= exists
    for name in DEPENDENCY_FILES:
        exists = (dll_dir / name).exists()
        log.line(f"  {'OK  ' if exists else 'WARN'}  {name}"
                 + ("" if exists else "  (CanonGB511.dll may fail to load without it)"))
    if not ok:
        log.line("FAIL: copy the missing files into this folder and rerun.")
        sys.exit(1)


def load_dll(dll_dir: Path, log: Log):
    log.section("STEP 1 — load CanonGB511.dll")
    log.line(f"Python: {platform.python_version()} "
             f"({platform.architecture()[0]}, {platform.machine()})")
    os.chdir(dll_dir)  # vendor DLL resolves gbdsp/gb501p relative to CWD
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        log.line("FAIL: not on Windows — this tool must run on the lab PC.")
        sys.exit(1)
    try:
        dll = loader(str(dll_dir / "CanonGB511.dll"))
    except OSError as exc:
        log.line(f"FAIL: could not load DLL: {exc}")
        log.line("Hints:")
        log.line("  - 32-bit DLL cannot load in 64-bit Python (and vice versa).")
        log.line("  - gb501p.dll must sit next to CanonGB511.dll.")
        log.line("  - A missing VC++ runtime also raises this error.")
        sys.exit(1)
    log.line("OK: DLL loaded.")
    declare_prototypes(dll)
    return dll


def declare_prototypes(dll) -> None:
    """Same prototypes as the working notebook (260220) init cell."""
    L, UL, DBL = ctypes.c_long, ctypes.c_ulong, ctypes.c_double
    PL, PUL, PDBL = (ctypes.POINTER(t) for t in (L, UL, DBL))
    protos = {
        "ctr_select_board": ((UL, UL), L),
        "ctr_load_program_file": ((ctypes.c_char_p,), L),
        "ctr_load_correction_file": ((ctypes.c_char_p, DBL, DBL, DBL, DBL, DBL), L),
        "ctr_read_status": ((PUL,), L),
        "ctr_goto_xy": ((L, L), L),
        "ctr_get_current_xy_pos": ((PL, PL), L),
        "ctr_get_xy_pos": ((PL, PL), L),
        "ctr_get_jump_speed": ((PDBL,), DBL),
        "ctr_set_jump_speed": ((DBL,), DBL),
        "ctr_set_start_list": ((UL,), L),
        "lst_jump_abs": ((L, L), L),
        "lst_long_delay": ((UL,), L),
        "lst_set_end_of_list": (None, L),
        "ctr_execute_list": ((UL,), L),
    }
    for name, (argtypes, restype) in protos.items():
        func = getattr(dll, name, None)
        if func is None:
            continue  # surface it later when actually used
        func.argtypes = argtypes
        func.restype = restype


# ----------------------------------------------------------------------
# Step 2/3: board bring-up
# ----------------------------------------------------------------------

def init_board(dll, port: int, board: int, scan: bool, log: Log) -> None:
    log.section("STEP 2 — DSP program + correction file")
    ret = dll.ctr_load_program_file(DSP_FILE.encode("cp932"))
    log.line(f"ctr_load_program_file({DSP_FILE!r}) -> {ret}"
             + ("  OK" if ret == 0 else "  FAIL (nonzero return code)"))
    ret = dll.ctr_load_correction_file(CORRECTION_FILE.encode("cp932"),
                                       1.0, 1.0, 0.0, 0.0, 0.0)
    log.line(f"ctr_load_correction_file({CORRECTION_FILE!r}) -> {ret}"
             + ("  OK" if ret == 0 else "  FAIL (nonzero return code)"))

    log.section("STEP 3 — select board")
    log.line("port: 0=PCIe, 1=LAN, 2=USB")
    if scan:
        found = []
        for p in (0, 1, 2):
            for b in (0, 1, 2, 3):
                ret = dll.ctr_select_board(p, b)
                log.line(f"ctr_select_board(port={p}, board={b}) -> {ret}")
                if ret == 0:
                    found.append((p, b))
        if not found:
            log.line("FAIL: no port/board combination answered.")
            log.line("  -> Is the Canon 'Galvano Scanner Control Software' still open?")
            log.line("     Close it: the board is single-client.")
            log.line("  -> Check the USB cable / board power.")
            sys.exit(1)
        port, board = found[0]
        log.line(f"Using first responding combination: port={port}, board={board}")
    ret = dll.ctr_select_board(port, board)
    log.line(f"ctr_select_board(port={port}, board={board}) -> {ret}"
             + ("  OK" if ret == 0 else "  FAIL"))
    if ret != 0:
        log.line("FAIL: board did not answer. Close the Canon control software,")
        log.line("check cabling/power, or rerun with --scan-board.")
        sys.exit(1)


# ----------------------------------------------------------------------
# Step 4: status and read-back
# ----------------------------------------------------------------------

def read_pos(dll, func_name: str = "ctr_get_current_xy_pos"):
    x, y = ctypes.c_long(), ctypes.c_long()
    ret = getattr(dll, func_name)(ctypes.byref(x), ctypes.byref(y))
    return ret, x.value, y.value


def check_status(dll, log: Log) -> None:
    log.section("STEP 4 — status and position read-back")
    status = ctypes.c_ulong()
    ret = dll.ctr_read_status(ctypes.byref(status))
    log.line(f"ctr_read_status -> ret={ret}, status=0x{status.value:08X}")

    speed = ctypes.c_double()
    try:
        ret = dll.ctr_get_jump_speed(ctypes.byref(speed))
        log.line(f"ctr_get_jump_speed -> ret={ret}, speed={speed.value}")
    except Exception as exc:  # noqa: BLE001
        log.line(f"ctr_get_jump_speed unavailable: {exc}")

    log.line("\n5x read of both position functions (0.2 s apart):")
    log.line(f"{'':4}{'current_xy_pos':>28}{'get_xy_pos':>28}")
    current_reads = []
    for i in range(5):
        ret1, x1, y1 = read_pos(dll, "ctr_get_current_xy_pos")
        try:
            ret2, x2, y2 = read_pos(dll, "ctr_get_xy_pos")
            second = f"ret={ret2} ({x2:>9}, {y2:>9})"
        except Exception as exc:  # noqa: BLE001
            second = f"unavailable: {exc}"
        log.line(f"  #{i}  ret={ret1} ({x1:>9}, {y1:>9})   {second}")
        current_reads.append((x1, y1))
        time.sleep(0.2)

    xs = {p[0] for p in current_reads}
    ys = {p[1] for p in current_reads}
    if xs == {SENTINEL} and ys == {SENTINEL}:
        log.line(f"\nWARNING: both axes read exactly {SENTINEL} (= -2^19) constantly.")
        log.line("  This is what a non-live read-back looks like: board held by the")
        log.line("  Canon software, drivers not in high-speed mode, or both axes railed.")


# ----------------------------------------------------------------------
# Step 5: motion response sweep
# ----------------------------------------------------------------------

def motion_sweep(dll, targets, settle_s: float, log: Log) -> None:
    log.section("STEP 5 — motion response sweep (ctr_goto_xy, absolute goto units)")
    log.line(f"Nominal conversion from the notebook: 1 goto unit = "
             f"{1.0 / CX_NOMINAL:.2f} read pulses (CX={CX_NOMINAL})")
    log.line(f"Targets (goto units, applied to X then Y): {targets}")
    log.line("Watch the beam/Canon monitor while this runs.\n")

    header = (f"{'axis':4} {'goto target':>12} {'ret':>4} "
              f"{'read X':>10} {'read Y':>10} {'Δread(axis)':>12} {'Δread/Δgoto':>12}")
    results = {"X": [], "Y": []}

    for axis in ("X", "Y"):
        log.line(f"--- {axis} axis ---")
        log.line(header)
        prev_goto = None
        prev_read = None
        for tgt in targets:
            # keep the other axis parked at 0 (origin) during the sweep
            gx, gy = (tgt, 0) if axis == "X" else (0, tgt)
            ret = dll.ctr_goto_xy(gx, gy)
            time.sleep(settle_s)
            _, xr, yr = read_pos(dll)
            moving = xr if axis == "X" else yr
            if prev_goto is not None and tgt != prev_goto:
                dread = moving - prev_read
                dgoto = tgt - prev_goto
                ratio = dread / dgoto
                results[axis].append(ratio)
                ratio_s = f"{ratio:12.3f}"
                dread_s = f"{dread:12d}"
            else:
                ratio_s, dread_s = " " * 12, " " * 12
            log.line(f"{axis:4} {tgt:>12} {ret:>4} {xr:>10} {yr:>10} {dread_s} {ratio_s}")
            prev_goto, prev_read = tgt, moving

    log.section("STEP 5 — summary")
    for axis in ("X", "Y"):
        ratios = [r for r in results[axis] if r != 0]
        if not results[axis]:
            log.line(f"{axis}: no data")
        elif not ratios:
            log.line(f"{axis}: NO MOTION — read-back never followed the commands.")
        else:
            mean = sum(ratios) / len(ratios)
            log.line(f"{axis}: MOVED. measured read-pulses per goto unit ≈ {mean:.3f} "
                     f"(nominal {1.0 / CX_NOMINAL:.3f}); "
                     f"{len(ratios)}/{len(results[axis])} steps produced motion")
    log.line("\nInterpretation:")
    log.line("  - ret != 0 on ctr_goto_xy      -> board refuses commands (bring-up issue).")
    log.line("  - ret == 0 but no read change  -> drivers not following: servo OFF or not")
    log.line("    in HIGH-SPEED SERIAL mode, or read-back not live. Fix via the Canon")
    log.line("    software (Servo ON + HiSpeed Start, then CLOSE it) or --rs232 COMx.")
    log.line("  - moved, ratio far from nominal -> unit conversion wrong: send me the log.")


def list_mode_test(dll, settle_s: float, log: Log) -> None:
    log.section("STEP 6 — list-mode move test (lst_jump_abs)")
    seq = [
        ("ctr_set_start_list", (1,)),
        ("lst_jump_abs", (1000, 1000)),
        ("lst_long_delay", (1000,)),
        ("lst_jump_abs", (0, 0)),
        ("lst_set_end_of_list", ()),
        ("ctr_execute_list", (1,)),
    ]
    for name, args in seq:
        func = getattr(dll, name, None)
        if func is None:
            log.line(f"{name}: not exported — skipping list-mode test.")
            return
        ret = func(*args)
        log.line(f"{name}{args} -> {ret}")
    time.sleep(settle_s)
    ret, x, y = read_pos(dll)
    log.line(f"read after list execution: ret={ret} ({x}, {y})")


# ----------------------------------------------------------------------
# Step 7 (optional): GC-211/212 RS-232 servo bring-up
# ----------------------------------------------------------------------

def rs232_bringup(port: str, baud: int, log: Log) -> None:
    log.section(f"STEP 7 — RS-232 servo bring-up on {port}")
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        log.line("FAIL: pyserial not installed (pip install pyserial). Skipping.")
        return

    def cmd(ser, axis: int, ident: int, data: int | None):
        payload = f"A{axis}C{ident:03d}"
        if data is not None:
            payload += f"/{data}"
        ser.write((payload + "\n").encode("ascii"))
        reply = ser.readline().decode("ascii", errors="replace").strip()
        log.line(f"  {payload:<14} -> {reply!r}")
        try:
            return int(reply.split("/", 1)[1])
        except (IndexError, ValueError):
            return None

    try:
        ser = serial.Serial(port=port, baudrate=baud, bytesize=8, parity="N",
                            stopbits=1, timeout=1.0, write_timeout=1.0)
    except Exception as exc:  # noqa: BLE001
        log.line(f"FAIL: cannot open {port}: {exc}")
        log.line("  (Is the Canon control software still holding the COM port?)")
        return
    with ser:
        for axis in (1, 2):
            cmd(ser, axis, 1, None)        # clear error
            cmd(ser, axis, 4, 1)           # servo ON
            status = cmd(ser, axis, 14, None)
            if status is not None and not status & 0x0002:  # not synced
                log.line(f"  axis {axis}: not synced -> homing (C002)")
                cmd(ser, axis, 2, None)
                time.sleep(2.0)
                cmd(ser, axis, 14, None)
            cmd(ser, axis, 15, None)       # error register
            cmd(ser, axis, 23, 7)          # switch to HIGH-SPEED serial
    log.line("RS-232 bring-up done: both axes servo ON + high-speed mode requested.")


# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", help="folder containing CanonGB511.dll etc.")
    parser.add_argument("--port", type=int, default=2, help="0=PCIe 1=LAN 2=USB (default 2)")
    parser.add_argument("--board", type=int, default=0, help="board index (default 0)")
    parser.add_argument("--scan-board", action="store_true",
                        help="try every port/board combination")
    parser.add_argument("--sweep", type=int, nargs="+", default=DEFAULT_SWEEP,
                        help=f"absolute goto-unit targets (default {DEFAULT_SWEEP})")
    parser.add_argument("--settle", type=float, default=0.3,
                        help="seconds to wait after each move (default 0.3)")
    parser.add_argument("--no-move", action="store_true",
                        help="diagnostics only, never command a move")
    parser.add_argument("--list-test", action="store_true",
                        help="also try a list-mode (lst_jump_abs) move")
    parser.add_argument("--rs232", metavar="COMx",
                        help="bring up GC-211/212 servos over RS-232 first")
    parser.add_argument("--rs232-baud", type=int, default=38400)
    args = parser.parse_args()

    log = Log()
    log.line(f"galvo_debug — {datetime.now().isoformat(timespec='seconds')}")
    log.line(f"log file: {log.path}")

    dll_dir = find_dll_dir(args.dir, log)
    check_files(dll_dir, log)
    dll = load_dll(dll_dir, log)
    init_board(dll, args.port, args.board, args.scan_board, log)

    if args.rs232:
        rs232_bringup(args.rs232, args.rs232_baud, log)

    check_status(dll, log)
    if not args.no_move:
        motion_sweep(dll, args.sweep, args.settle, log)
        if args.list_test:
            list_mode_test(dll, args.settle, log)

    log.section("DONE")
    log.line(f"Full log saved to: {log.path}")
    log.line("Send this file back for analysis.")


if __name__ == "__main__":
    main()

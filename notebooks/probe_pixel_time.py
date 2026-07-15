"""Time-per-pixel probe: wraps galvo/nea calls, prints where the time goes.

Usage:
    python probe_pixel_time.py                    # mock backends, 4x4 grid
    python probe_pixel_time.py --real              # real hardware backends
    python probe_pixel_time.py --nb-z 3 --dz-nm 500  # 3-D scan (Z stack)

ponytail: standalone script, no test framework — just needs one run to read timings.
"""
from __future__ import annotations

import argparse
import statistics
import time
from collections import defaultdict
from functools import wraps

from galvo_gui.motion.base import GalvoError, run_raster_scan


def timed(bucket: dict, name: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = fn(*args, **kwargs)
            bucket[name].append(time.perf_counter() - t0)
            return result
        return wrapper
    return deco


def timed_move_z(bucket: dict, name: str, nea, z_bucket: dict):
    """Like ``timed``, but also pulls the backend's per-step Z breakdown
    (open_mirror / go_relative / wait_for_mirror / read_absolute_position /
    close_mirror) into *z_bucket* after each move, when the backend exposes it."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = fn(*args, **kwargs)
            bucket[name].append(time.perf_counter() - t0)
            sub = getattr(nea, "last_z_move_timings", lambda: {})()
            for key, value in sub.items():
                z_bucket[key].append(value)
            return result
        return wrapper
    return deco


def instrument(galvo, nea, bucket: dict, z_bucket: dict):
    galvo.move_relative_pulses = timed(bucket, "move_relative_pulses")(galvo.move_relative_pulses)
    galvo.read_xy_pulses = timed(bucket, "read_xy_pulses")(galvo.read_xy_pulses)
    nea.read_sample = timed(bucket, "read_sample")(nea.read_sample)
    nea.move_z_relative = timed_move_z(bucket, "move_z_relative", nea, z_bucket)(nea.move_z_relative)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="use real hardware backends")
    parser.add_argument("--nb", type=int, default=4, help="grid side (nb x nb)")
    parser.add_argument("--twait", type=float, default=0.0)
    parser.add_argument("--t-integ", type=float, default=0.4)
    parser.add_argument("--host", default="nea-server")
    parser.add_argument("--nb-z", type=int, default=1, help="Z slices (>1 for a 3-D scan)")
    parser.add_argument("--dz-nm", type=float, default=0.0, help="Z step between slices, in nm")
    parser.add_argument(
        "--no-offset",
        action="store_true",
        help="disable galvo XY offset correction (workaround for a spurious "
             "auto-calibrated offset that pushes moves off-target)",
    )
    args = parser.parse_args()
    is_3d = args.nb_z > 1

    if args.real:
        from galvo_gui.motion.galvo_nea import RealGalvoBackend, RealNeaBackend
        galvo, nea = RealGalvoBackend(), RealNeaBackend()
        galvo.connect()
        nea.connect(args.host)
    else:
        from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend
        galvo, nea = MockGalvoBackend(), MockNeaBackend()
        galvo.connect()
        nea.connect()

    def show_offset(label: str) -> None:
        print(f"[{label}] offset_x_p={getattr(galvo, '_offset_x_p', '?')} "
              f"offset_y_p={getattr(galvo, '_offset_y_p', '?')} "
              f"enabled={galvo.offset_correction_enabled()}")

    show_offset("after connect/calibration")
    if args.no_offset:
        galvo.set_offset_correction_enabled(False)
        show_offset("after --no-offset")

    bucket: dict[str, list[float]] = defaultdict(list)
    z_bucket: dict[str, list[float]] = defaultdict(list)
    instrument(galvo, nea, bucket, z_bucket)

    pixel_t0 = time.perf_counter()
    pixel_times: list[float] = []

    def on_point(ix, iy, sample):
        nonlocal pixel_t0
        now = time.perf_counter()
        pixel_times.append(now - pixel_t0)
        pixel_t0 = now

    def dump_move_diag(context: str) -> None:
        """On a galvo move failure, print the backend's per-move diagnostics
        (offset applied, goto units commanded, before/after read-back) so a
        systematic miss can be read off instead of guessed at."""
        diag = getattr(galvo, "last_move_diagnostics", lambda: {})()
        print(f"\n!!! galvo move failed during {context}")
        if diag:
            for key, value in diag.items():
                print(f"    {key:<24}{value}")
        print(f"    offset_x_p (applied)    {getattr(galvo, '_offset_x_p', '?')}")
        print(f"    offset_y_p (applied)    {getattr(galvo, '_offset_y_p', '?')}")
        print(f"    offset_enabled          {getattr(galvo, '_offset_enabled', '?')}")
        print(f"    x_goto_bias             {getattr(galvo, '_x_goto_bias', '?')}")

    if is_3d:
        # Mirror ScanWorker.run(): centre the stack on the current focus,
        # step down through it, then restore Z at the end.
        half_span = args.dz_nm * (args.nb_z - 1) / 2.0
        nea.move_z_relative(-half_span)
        z_moved_nm = -half_span
        try:
            for iz in range(args.nb_z):
                show_offset(f"start of Z slice {iz}")
                try:
                    run_raster_scan(
                        galvo, nea,
                        dx_pulses=1000.0, dy_pulses=1000.0,
                        nb_x=args.nb, nb_y=args.nb,
                        twait=args.twait, t_integ_s=args.t_integ,
                        on_point=on_point, stop_check=lambda: False,
                    )
                except GalvoError:
                    dump_move_diag(f"Z slice {iz}/{args.nb_z - 1}")
                    raise
                if iz < args.nb_z - 1:
                    nea.move_z_relative(args.dz_nm)
                    z_moved_nm += args.dz_nm
                    # First pixel of each new slice includes the inter-slice Z
                    # move; reset the pixel clock so it isn't charged to pixel-
                    # to-pixel XY timing.
                    pixel_t0 = time.perf_counter()
        finally:
            nea.move_z_relative(-z_moved_nm)
    else:
        try:
            run_raster_scan(
                galvo, nea,
                dx_pulses=1000.0, dy_pulses=1000.0,
                nb_x=args.nb, nb_y=args.nb,
                twait=args.twait, t_integ_s=args.t_integ,
                on_point=on_point, stop_check=lambda: False,
            )
        except GalvoError:
            dump_move_diag("2-D raster")
            raise

    grid_desc = f"{args.nb}x{args.nb}x{args.nb_z}" if is_3d else f"{args.nb}x{args.nb}"
    print(f"\n--- {'real' if args.real else 'mock'} backend, {grid_desc} grid, "
          f"twait={args.twait}, t_integ={args.t_integ}"
          f"{f', dz={args.dz_nm}nm' if is_3d else ''} ---\n")
    print(f"{'call':<24}{'n':>5}{'mean(ms)':>12}{'min(ms)':>10}{'max(ms)':>10}")
    for name, times in bucket.items():
        print(f"{name:<24}{len(times):>5}{statistics.mean(times)*1e3:>12.2f}"
              f"{min(times)*1e3:>10.2f}{max(times)*1e3:>10.2f}")

    if z_bucket:
        # Break the ~0.7 s move_z_relative down into its hardware sub-steps so
        # the dominant one is obvious. Ordered as they run inside the move.
        order = ["open_mirror", "go_relative", "wait_for_mirror",
                 "read_absolute_position", "close_mirror", "total"]
        print(f"\nmove_z_relative breakdown:")
        for key in order:
            times = z_bucket.get(key)
            if not times:
                continue
            print(f"  {key:<22}{len(times):>5}{statistics.mean(times)*1e3:>12.2f}"
                  f"{min(times)*1e3:>10.2f}{max(times)*1e3:>10.2f}")

    if len(pixel_times) > 1:
        # first pixel includes the start-corner move; skip it
        rest = pixel_times[1:]
        print(f"\n{'pixel-to-pixel':<24}{len(rest):>5}{statistics.mean(rest)*1e3:>12.2f}"
              f"{min(rest)*1e3:>10.2f}{max(rest)*1e3:>10.2f}")

    accounted = sum(statistics.mean(t) for t in bucket.values() if t)
    observed = statistics.mean(pixel_times[1:]) if len(pixel_times) > 1 else 0.0
    print(f"\nsum of instrumented calls: {accounted*1e3:.2f} ms  "
          f"vs observed pixel time: {observed*1e3:.2f} ms  "
          f"(gap = {(observed-accounted)*1e3:.2f} ms -> GUI/thread/uninstrumented overhead)")


if __name__ == "__main__":
    main()

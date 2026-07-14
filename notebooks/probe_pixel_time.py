"""Time-per-pixel probe: wraps galvo/nea calls, prints where the time goes.

Usage:
    python probe_pixel_time.py            # mock backends, 4x4 grid
    python probe_pixel_time.py --real      # real hardware backends

ponytail: standalone script, no test framework — just needs one run to read timings.
"""
from __future__ import annotations

import argparse
import statistics
import time
from collections import defaultdict
from functools import wraps

from galvo_gui.motion.base import run_raster_scan


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


def instrument(galvo, nea, bucket: dict):
    galvo.move_relative_pulses = timed(bucket, "move_relative_pulses")(galvo.move_relative_pulses)
    galvo.read_xy_pulses = timed(bucket, "read_xy_pulses")(galvo.read_xy_pulses)
    nea.read_sample = timed(bucket, "read_sample")(nea.read_sample)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="use real hardware backends")
    parser.add_argument("--nb", type=int, default=4, help="grid side (nb x nb)")
    parser.add_argument("--twait", type=float, default=0.0)
    parser.add_argument("--t-integ", type=float, default=0.4)
    parser.add_argument("--host", default="nea-server")
    args = parser.parse_args()

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

    bucket: dict[str, list[float]] = defaultdict(list)
    instrument(galvo, nea, bucket)

    pixel_t0 = time.perf_counter()
    pixel_times: list[float] = []

    def on_point(ix, iy, sample):
        nonlocal pixel_t0
        now = time.perf_counter()
        pixel_times.append(now - pixel_t0)
        pixel_t0 = now

    run_raster_scan(
        galvo, nea,
        dx_pulses=1000.0, dy_pulses=1000.0,
        nb_x=args.nb, nb_y=args.nb,
        twait=args.twait, t_integ_s=args.t_integ,
        on_point=on_point, stop_check=lambda: False,
    )

    print(f"\n--- {'real' if args.real else 'mock'} backend, {args.nb}x{args.nb} grid, "
          f"twait={args.twait}, t_integ={args.t_integ} ---\n")
    print(f"{'call':<24}{'n':>5}{'mean(ms)':>12}{'min(ms)':>10}{'max(ms)':>10}")
    for name, times in bucket.items():
        print(f"{name:<24}{len(times):>5}{statistics.mean(times)*1e3:>12.2f}"
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

"""A/B verdict with its uncertainty, from the per-frame rows of runs with MKW_AB (MKW_FRAMES, runtime/core/diag/frames.c).

Why: ab.py compared 60-frame windows and printed B - A without its error; a 20 s race gave 4-6 windows per side and
of 85 A/B runs to 25/11/2026 only 18 differed by more than two standard errors, so most conclusions were noise read
as a result. Here each MKW_AB block of world clock is one sample (frames inside a block are not independent: a slow
stretch lasts many frames), its first --settle frames left out (the switch's own transient), and adjacent blocks make
pairs: the order A B | B A | A B ... puts a linear trend of the scene once on each sign, so it cancels in the mean of
the pairs' B - A and only widens their spread. The interval is Student's t at 95 % over the pairs. Several files (a
plain run and its MKW_AB_INVERT twin, or more) pool their pairs.

The frame interval is paced to whole vblanks (33.3, 50, 66.7 ms at 60 Hz), so its block means jump in steps and a
change smaller than a step shows only as how often the pace drops; --metric picks another column, and the text
output prints every column's verdict (aa1, 25/11/2026: the same 22 pairs gave ±3.2 ms on the interval, ±1.2 on replay).

Usage: uv run tools/runner/abstat.py build/run/NAME.frames.tsv [...] [--settle N] [--target MS] [--metric COL] [--json]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Student's t, two-sided 95 %, by degrees of freedom; past 30 the normal 1.96 is within 2 %.
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
       2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]
MIN_PAIRS = 3   # below this the spread itself is a guess
# frames.tsv columns in microseconds: the frame interval, the replay thread's recording CPU, and the render thread's waits
METRICS = ("interval", "replay", "pipe", "tex", "fence", "pace")
# A block's value is its 20 % trimmed mean: a stutter of a few 200 ms frames in a 1 s block moved its mean by more than
# the effects measured (gpaa1, A/A in a Grand Prix, 25/11/2026, 1 s blocks: replay ±3.4 ms with the mean, ±0.74 trimmed)
TRIM = 0.2


def trimmed(xs: list[float], cut: float = TRIM) -> float:
    s = sorted(xs)
    k = int(len(s) * cut)
    return statistics.fmean(s[k : len(s) - k] or s)


def t95(df: int) -> float:
    return T95[df - 1] if 1 <= df <= len(T95) else 1.96


@dataclass
class Block:
    index: int        # world clock / period
    side: int         # 0: A, 1: B
    frames: int       # counted, after the settle
    mean_ms: float    # trimmed mean of the metric (TRIM)


@dataclass
class Verdict:
    pairs: int
    a_ms: float               # mean frame interval of the A blocks
    b_ms: float
    diff_ms: float            # mean of the pairs' B - A
    half_ms: float | None     # half-width of the 95 % interval; None below MIN_PAIRS
    pct: float                # diff against A, in %
    significant: bool
    pairs_for_target: int | None   # pairs the --target half-width needs at this spread
    text: str


def read_rows(path: Path) -> list[dict[str, float]]:
    """The rows, less a last one cut mid-line (a killed run stops wherever its 60-frame flush was)."""
    rows = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            try:
                rows.append({k: float(v) for k, v in row.items()})
            except (TypeError, ValueError):
                break
    return rows


def period_ms(rows: list[dict[str, float]]) -> float | None:
    """MKW_AB's block length: the world clock at the first change of side (A B B A starts with a whole block)."""
    clocked = [r for r in rows if r["world_ms"] >= 0]
    for prev, cur in zip(clocked, clocked[1:]):
        if cur["ab"] != prev["ab"]:
            return round(cur["world_ms"] / 100) * 100   # the change lands on the first present past the boundary
    return None


def blocks(rows: list[dict[str, float]], settle: int, metric: str = "interval") -> list[Block]:
    col = f"{metric}_us"
    period = period_ms(rows)
    if not period:
        return []
    groups: dict[int, list[dict[str, float]]] = {}
    for r in rows:
        if r["world_ms"] >= 0:
            groups.setdefault(int(r["world_ms"] // period), []).append(r)
    out = []
    for k, rs in sorted(groups.items()):
        kept = rs[settle:]
        span = rs[-1]["world_ms"] - rs[0]["world_ms"]
        if not kept or span < 0.5 * period:   # the run's last block, cut short
            continue
        side = statistics.mode(int(r["ab"]) for r in kept)
        out.append(Block(k, side, len(kept), trimmed([r[col] for r in kept]) / 1000))
    return out


def pair_diffs(bs: list[Block]) -> list[float]:
    """B - A of blocks (0,1), (2,3)...: each pair holds one block of each side."""
    by_index = {b.index: b for b in bs}
    diffs = []
    for k in range(0, max(by_index, default=-1) + 1, 2):
        x, y = by_index.get(k), by_index.get(k + 1)
        if x and y and x.side != y.side:
            a, b = (x, y) if x.side == 0 else (y, x)
            diffs.append(b.mean_ms - a.mean_ms)
    return diffs


def verdict(diffs: list[float], bs: list[Block], target: float | None = None) -> Verdict:
    a = [b.mean_ms for b in bs if b.side == 0]
    b = [x.mean_ms for x in bs if x.side == 1]
    a_ms = statistics.fmean(a) if a else math.nan
    b_ms = statistics.fmean(b) if b else math.nan
    n = len(diffs)
    if n == 0:
        return Verdict(0, a_ms, b_ms, math.nan, None, math.nan, False, None,
                       "no A/B pair: MKW_AB off, or the run ended before two blocks")
    d = statistics.fmean(diffs)
    pct = d / a_ms * 100 if a and a_ms else math.nan   # a wait can be 0 on one side
    if n < MIN_PAIRS:
        return Verdict(n, a_ms, b_ms, d, None, pct, False, None,
                       f"B - A {d:+.2f} ms over {n} pair(s): too few to judge (at least {MIN_PAIRS})")
    sd = statistics.stdev(diffs)
    half = t95(n - 1) * sd / math.sqrt(n)
    need = math.ceil((1.96 * sd / target) ** 2) if target and sd > 0 else None
    sig = abs(d) > half
    if sig:
        text = f"B {'slower' if d > 0 else 'faster'}: {d:+.2f} ms per frame ±{half:.2f} (95 %, {n} pairs), {pct:+.1f} %"
    else:
        text = f"no difference past ±{half:.2f} ms per frame (B - A {d:+.2f}, 95 %, {n} pairs)"
    if need and target and half > target:
        text += f"; ±{target:.2f} needs ~{need} pairs"
    return Verdict(n, a_ms, b_ms, d, half, pct, sig, need, text)


def analyse(paths: list[Path], settle: int = 5, target: float | None = None,
            metric: str = "interval") -> tuple[Verdict, list[str]]:
    diffs: list[float] = []
    every: list[Block] = []
    lines = []
    for p in paths:
        bs = blocks(read_rows(p), settle, metric)
        ds = pair_diffs(bs)
        diffs += ds
        every += bs
        lines.append(f"{p.name}: {len(bs)} blocks, {len(ds)} pairs, B - A per pair: "
                     + (" ".join(f"{x:+.1f}" for x in ds) or "none"))
    return verdict(diffs, every, target), lines


def by_metric(paths: list[Path], settle: int = 5, target: float | None = None) -> dict[str, Verdict]:
    """Every column's verdict: a change can move the replay or a wait without moving the paced interval. Only the
    chosen metric decides: with six columns at 95 %, one of them reads "significant" by chance in about a quarter of
    A/A runs (gpaa1: fence "B faster", 25/11/2026)."""
    return {m: analyse(paths, settle, target, m)[0] for m in METRICS}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("frames", nargs="+", type=Path, help="NAME.frames.tsv of runs with MKW_AB")
    ap.add_argument("--settle", type=int, default=5, help="frames left out at the start of each block (5)")
    ap.add_argument("--target", type=float, help="the half-width wanted, ms: prints how many pairs it needs")
    ap.add_argument("--metric", choices=METRICS, default="interval", help="the column judged (interval)")
    ap.add_argument("--json", action="store_true", help="the verdict as JSON")
    args = ap.parse_args()
    v, lines = analyse(args.frames, args.settle, args.target, args.metric)
    if args.json:
        json.dump(asdict(v), sys.stdout, indent=1)
        print()
        return
    print("\n".join(lines))
    print(f"{args.metric}: A {v.a_ms:.2f} ms, B {v.b_ms:.2f} ms per frame; {v.text}")
    for m, o in by_metric(args.frames, args.settle, args.target).items():
        if m != args.metric:
            print(f"  {m}: A {o.a_ms:.2f}, B {o.b_ms:.2f}; {o.text}")


if __name__ == "__main__":
    main()

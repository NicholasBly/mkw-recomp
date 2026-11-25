"""Follows a run's log and prints only what changes the plan: FATAL and guard stops, the lines of a pattern if asked,
STALLED (the frame stays put for 60 s: a frozen game shows a black window) and BLACK (the window's image stays black
for 2 min while frames advance), then one end line with the run's outcome. Every printed line wakes an agent for a
whole turn: the old default of every `seq: enter` cost 13 % of a week's tokens (09-16/11/2026).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import ctl, logread
from paths import ROOT, Run

ALWAYS = r"FATAL|GPU_GUARD:.*destroyed|device fault"


@dataclass
class Timing:
    poll: float = 2.0         # log read
    stall_every: float = 15.0
    stall_after: int = 4      # checks with the same frame: 60 s
    black_every: float = 30.0
    black_after: int = 4      # black checks in a row: 2 min


def maxlum(png: Path) -> int | None:
    r = subprocess.run(["uv", "run", "-q", str(ROOT / "tools/imagen/maxlum.py"), str(png)], capture_output=True,
                       text=True)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def save_stacks(run: Run) -> Path:
    out = run.dir / f"{run.name}-stall-stacks.txt"
    with out.open("w") as f:
        subprocess.run(["uv", "run", str(ROOT / "tools/run/stacks.py"), "--host", "--save",
                        str(run.dir / f"{run.name}-stall.txt")], stdout=f, stderr=subprocess.STDOUT, timeout=120,
                       check=False)
    return out


def watch(run: Run, pattern: str | None = None, *, timing: Timing | None = None, black: bool = True,
          alive: Callable[[], bool] | None = None, say: Callable[[str], None] = print,
          stacks: Callable[[Run], Path] = save_stacks, lum: Callable[[Path], int | None] = maxlum) -> None:
    t = timing or Timing()
    pat = re.compile(ALWAYS + (f"|{pattern}" if pattern else ""))
    alive = alive or (lambda: bool(ctl.run_pids(run)) or bool(ctl.units(run)))
    end = time.monotonic() + 120
    while not run.log.exists():
        if time.monotonic() >= end:
            say(f"watch: no {run.log} after 2 min")
            return
        time.sleep(1)
    pos = 0
    last, same, stalled = -1, 0, False
    dark, blacked = 0, False
    next_stall = time.monotonic() + t.stall_every
    next_black = time.monotonic() + t.black_every

    def drain() -> None:
        nonlocal pos
        with run.log.open("rb") as f:
            f.seek(pos)
            data = f.read()
        cut = data.rfind(b"\n") + 1   # a line still being written waits for the next read
        for ln in data[:cut].decode(errors="replace").splitlines():
            if pat.search(ln):
                say(ln.split("] ", 1)[-1])
        pos += cut

    while True:
        drain()
        now = time.monotonic()
        if not stalled and now >= next_stall:
            next_stall = now + t.stall_every
            f = ctl.frame(run) if run.fifo.exists() else None
            same = same + 1 if f is not None and f == last and f > 0 else 0
            last = f if f is not None else last
            if same >= t.stall_after:
                stalled = True
                out = stacks(run)
                say(f"watch: {run.name} STALLED at frame {f} for {t.stall_every * t.stall_after:.0f} s (stacks: {out})")
        if black and not blacked and not stalled and same == 0 and last > 0 and now >= next_black:
            next_black = now + t.black_every
            png = run.dir / f"{run.name}-watch.png"
            png.unlink(missing_ok=True)
            try:
                ctl.send(run, ["screen", str(png)], timeout=20)
                v = lum(png) if png.exists() else None
            except ctl.NotRunning:
                v = None
            if v is not None:
                dark = dark + 1 if v <= 10 else 0
                if dark >= t.black_after:
                    blacked = True
                    say(f"watch: {run.name} BLACK at frame {last}: the window stayed black while frames advance ({png})")
        if not alive():
            time.sleep(min(2, t.poll))
            drain()
            facts = logread.read(run.log)
            say(f"watch: {run.name} ended: {facts.end_line or 'no end line'}; summary {run.json}")
            return
        time.sleep(t.poll)

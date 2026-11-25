"""ctl commands when the game enters one of its phases (sequence nodes, MKW_SEQ_WATCH=1: runtime/core/diag/seqwatch.c),
not at a frame number that moves with every change of pacing.

Trigger: NODE, or PREV,NODE (the first NODE after PREV entered) or -PREV,NODE (after PREV left), as MKW_SEQ_UNTIL;
IsSequenceFlag also enters in the menus, the race's comes after the cup movie. Offsets +F[,F...] run the commands F
presents after, {k} in an argument becoming that offset. Node names: build/seq_nodes.tsv (third column).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import ctl
from paths import Run


class AtError(Exception):
    pass


def parse_trigger(spec: str) -> tuple[str | None, str | None, str]:
    """(enter PREV, leave PREV, NODE)."""
    if "," not in spec:
        if spec.startswith("-"):   # 06/11/2026: two runs lost waiting for a node named "-WaitIntroMovie"
            raise AtError(f"{spec}: a leave is PREV,NODE (-WaitIntroMovie,IsSequenceFlag), not a node")
        return None, None, spec
    prev, node = spec.split(",", 1)
    return (None, prev[1:], node) if prev.startswith("-") else (prev, None, node)


def wait_seq(run: Run, offset: int, what: str, node: str, alive: Callable[[], bool], poll: float = 0.2) -> tuple[int, int]:
    """Waits for "seq: WHAT NODE frame N" past byte OFFSET of the log: (frame, offset just after that line)."""
    pat = re.compile(rf"seq: {what} {re.escape(node)} frame (\d+)")
    while True:
        with run.log.open("rb") as f:
            f.seek(offset)
            data = f.read()
        pos = 0
        for raw in data.splitlines(keepends=True):
            pos += len(raw)
            if raw.endswith(b"\n") and (m := pat.search(raw.decode(errors="replace"))):
                return int(m[1]), offset + pos
        if not alive():
            raise AtError(f"at: the run ended before {what} {node}")
        time.sleep(poll)


def commands(argv: list[str], k: int) -> list[list[str]]:
    out, cur = [], []
    for a in [*argv, "--"]:
        if a == "--":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(a.replace("{k}", str(k)))
    return out


def at(run: Run, spec: str, offsets: list[int], argv: list[str], past: bool = False,
       alive: Callable[[], bool] | None = None, say: Callable[[str], None] = print, wait_log: float = 60) -> None:
    enter_prev, leave_prev, node = parse_trigger(spec)
    if not argv:
        raise AtError("at: no command")
    alive = alive or (lambda: bool(ctl.run_pids(run)) or bool(ctl.units(run)))
    end = time.monotonic() + wait_log
    while not run.log.exists():   # started right before: the log comes a moment later
        if time.monotonic() >= end:
            raise AtError(f"at: no {run.log}")
        time.sleep(0.2)
    offset = 0 if past else run.log.stat().st_size
    if enter_prev:
        _, offset = wait_seq(run, offset, "enter", enter_prev, alive)
    if leave_prev:
        _, offset = wait_seq(run, offset, "leave", leave_prev, alive)
    f0, _ = wait_seq(run, offset, "enter", node, alive)
    if not run.fifo.exists():
        raise AtError(f"at: {run.name} has no control FIFO ({run.fifo}): started without MKW_CTL")
    say(f"at: {node} entered at frame {f0}")
    for k in offsets:
        while (f := ctl.frame(run)) is None or f < f0 + k:
            if f is None and not alive():
                raise AtError(f"at: the run ended before frame {f0 + k}")
            time.sleep(0.03)
        for cmd in commands(argv, k):
            say(ctl.send(run, cmd))

#!/usr/bin/env python3
"""Game runs on this PC for agents: start one, talk to it, follow it, and read how it went from build/run/NAME.json.

Why: the run layer was eight bash scripts without tests (race.sh, run_perf.sh, live.sh, ctl.sh, at.sh, watch.sh...),
fragile in ways found one run at a time, and an A/B gave a number without its error. This is the same layer in Python,
tested against a fake runtime (tests/run/), with one summary per run and an A/B that stops once it is measured.

Usage:
  run.py race NAME [SECONDS] [--kind race|tt|gp|plain]  up to the race, RACE_SECS (20) of it, then out (plain: no
                                                   race defaults, WORLD_SECS 30, 120 s); NAME.json at the end
  run.py live NAME [SECONDS]                       stays up (900 s) for ctl; same checks
  run.py ctl NAME COMMAND [ARG...]                 frame | shot PATH | screen PATH | dump DIR [ubo|lut] | flight | heap |
                                                   press <ticks> <button> [hold] | exit | stop | ...
  run.py at [--past] NAME [PREV,]NODE [+F[,F...]] COMMAND [ARG...] [-- COMMAND...]
  run.py watch NAME [PATTERN]                      the Monitor of a run: failures, STALLED, BLACK, then its end
  run.py summary NAME [...]                        rewrites and prints NAME.json from the run's files
Every MKW_* passes through. An A/B: MKW_AB=<s> MKW_<X>=ab, and MKW_AB_TARGET=<ms> stops the race once B - A's 95 %
half-width on MKW_AB_METRIC (interval, or replay for a change to the recording's CPU) is that small (RACE_SECS stays
the limit); the verdict is in NAME.json ("ab", every column in "ab.by_metric") and from tools/runner/abstat.py.
Under Claude Code a run needs MKW_RUN_WHY (what I expect; what I conclude from each result).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import at, ctl, launch, report, watch
from paths import Run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 2)[2])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("race")
    p.add_argument("name")
    p.add_argument("seconds", nargs="?", type=int)
    p.add_argument("--kind", choices=["race", "tt", "gp", "plain"],
                   default="tt" if os.environ.get("RACE_TT") == "1" else "gp" if os.environ.get("RACE_GP") == "1"
                   else "race")
    p = sub.add_parser("live")
    p.add_argument("name")
    p.add_argument("seconds", nargs="?", type=int)
    p = sub.add_parser("ctl")
    p.add_argument("name")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("at")
    p.add_argument("--past", action="store_true", help="a phase entered before now counts")
    p.add_argument("name")
    p.add_argument("trigger")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p = sub.add_parser("watch")
    p.add_argument("name")
    p.add_argument("pattern", nargs="?")
    p = sub.add_parser("summary")
    p.add_argument("names", nargs="+")
    p.add_argument("--json", action="store_true", help="print the JSON, not its lines")
    a = ap.parse_args(argv)

    if a.cmd in ("race", "live"):
        plan = launch.make_plan(a.name, a.kind if a.cmd == "race" else "live", a.seconds)
        doc = launch.race(plan) if a.cmd == "race" else launch.live(plan)
        return 1 if doc.get("outcome") == "refused" else 0
    if a.cmd == "ctl":
        if not a.command:
            ap.error("ctl: no command")
        try:
            print(ctl.stop(Run(a.name)) if a.command == ["stop"] else ctl.send(Run(a.name), a.command))
        except ctl.NotRunning as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if a.cmd == "at":
        rest = a.rest
        offsets = [0]
        if rest and rest[0].startswith("+"):
            offsets = [int(x) for x in rest[0][1:].split(",")]
            rest = rest[1:]
        try:
            at.at(Run(a.name), a.trigger, offsets, rest, past=a.past)
        except (at.AtError, ctl.NotRunning) as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    if a.cmd == "watch":
        watch.watch(Run(a.name), a.pattern, black=os.environ.get("WATCH_BLACK", "1") == "1")
        report_now(Run(a.name))
        return 0
    if a.cmd == "summary":
        for n in a.names:
            doc = report_now(Run(n))
            print(json.dumps(doc, indent=1, ensure_ascii=False) if a.json else "\n".join(report.lines(doc)))
        return 0
    return 2


def report_now(run: Run) -> dict:
    """NAME.json again from the files: keeps what launch wrote (binary, env, why), reads the end afresh."""
    try:
        meta = json.loads(run.json.read_text())
    except (OSError, ValueError):
        meta = {"name": run.name}
    if meta.get("outcome") not in (None, "running"):   # written by its launcher at the end: rc and signal are there
        return meta
    keep = {k: meta[k] for k in ("name", "kind", "why", "binary", "env", "seconds", "started", "started_unix", "stop")
            if k in meta}
    keep.setdefault("name", run.name)
    if ctl.run_pids(run) or ctl.units(run):
        return report.write(run, keep, outcome="running")
    started, limit = meta.get("started_unix"), meta.get("seconds")
    # the log's last write, not now: a run summed up an hour after a crash did not time out
    last = run.log.stat().st_mtime if run.log.exists() else 0
    timed_out = bool(started and limit and last - started >= limit - 2)
    return report.write(run, keep, timed_out=timed_out)


if __name__ == "__main__":
    sys.exit(main())

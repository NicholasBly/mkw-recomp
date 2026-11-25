"""What a run's log says, as data: phases entered, the race start, the end line and how the run ended.

The end is one of a closed list (OUTCOMES), so an agent reads one word instead of grepping: before 25/11/2026 one run
in seven (65 of 475) left no end line and each was read by hand to tell a timeout from an OOM or a ctl stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = {
    "exit": "the run ended itself: MKW_SEQ_EXIT, ctl exit, --seconds or the game's own end",
    "window_closed": "the window was closed",
    "fatal": "a FATAL or fatal signal line: the runtime stopped on an error",
    "crashed": "the process ended without an end line nor a kill (a crash the runtime did not log)",
    "killed_timeout": "killed at the wall-clock limit (SECONDS)",
    "killed_oom": "killed before its limit by a SIGKILL nobody asked for: the job's memory cap or the OOM killer",
    "stopped": "stopped on purpose (run.py ctl NAME stop)",
    "refused": "not started: a check failed (stale build, memory, GPU, MKW_RUN_WHY, another run)",
    "running": "still running",
}

SEQ = re.compile(r"seq: (enter|leave) (\w+) frame (\d+)(?: at ([\d.]+) s)?")
RACE = re.compile(r"seq: watch off at the start of (\w+) frame (\d+)")
FRAME = re.compile(r"(?:perf|present|vk_exec|seq: \w+ \w+|ctl: \w+)\D*frame (\d+)")
END = re.compile(r"seq: (?:MKW_SEQ_EXIT|ctl exit).*: exit|frametime: exit|guest ExitProcess|plugin: game ending")
CLOSED = re.compile(r"window closed; exiting")
FATAL = re.compile(r"FATAL: |fatal signal|in lifted function")


@dataclass
class LogFacts:
    phases: list[dict] = field(default_factory=list)   # {"node", "frame", "s"}: each enter, in order
    race_start: dict | None = None                      # {"node", "frame"}: MKW_SEQ_UNTIL's event
    last_frame: int = 0
    end_line: str | None = None
    closed: bool = False
    fatal: list[str] = field(default_factory=list)


def body(line: str) -> str:
    return line.split("] ", 1)[-1].strip()


def read(path: Path) -> LogFacts:
    f = LogFacts()
    if not path.exists():
        return f
    for line in path.read_text(errors="replace").splitlines():
        if m := SEQ.search(line):
            if m[1] == "enter":
                f.phases.append({"node": m[2], "frame": int(m[3]), "s": float(m[4]) if m[4] else None})
        if (m := RACE.search(line)) and f.race_start is None:
            f.race_start = {"node": m[1], "frame": int(m[2])}
        for m in FRAME.finditer(line):
            f.last_frame = max(f.last_frame, int(m[1]))
        if FATAL.search(line) and len(f.fatal) < 3:
            f.fatal.append(body(line)[:200])
        if f.end_line is None and END.search(line):
            f.end_line = body(line)[:200]
        if CLOSED.search(line):
            f.closed = True
            f.end_line = f.end_line or body(line)
    return f


def outcome(f: LogFacts, *, signal: int | None, timed_out: bool, stopped: bool) -> str:
    """The run's end. signal: the one that killed the process (9 for SIGKILL), or None when it exited."""
    if f.fatal:
        return "fatal"
    if f.closed:
        return "window_closed"
    if f.end_line:
        return "exit"
    if stopped:
        return "stopped"
    if timed_out:
        return "killed_timeout"
    if signal == 9:
        return "killed_oom"
    return "crashed"

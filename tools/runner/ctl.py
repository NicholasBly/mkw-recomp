"""Commands to a running game through its control FIFO (MKW_CTL, runtime/core/base/ctl.c), each answered by a
"ctl: ..." line of its log; `stop` ends the run's job.

The FIFO is opened without blocking: with no reader (the run ended, or never opened it) that fails at once instead of
hanging the caller, which the shell version did on a run that had died.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from paths import Run

ANSWER = re.compile(r"(?:ctl|freeze): ")


class NotRunning(Exception):
    pass


def log_size(run: Run) -> int:
    try:
        return run.log.stat().st_size
    except FileNotFoundError:
        return 0


def new_lines(run: Run, offset: int) -> list[str]:
    with run.log.open("rb") as f:
        f.seek(offset)
        return f.read().decode(errors="replace").splitlines()


def wait_line(run: Run, offset: int, pattern: re.Pattern[str], timeout: float) -> str | None:
    end = time.monotonic() + timeout
    while True:
        for ln in new_lines(run, offset):
            if pattern.search(ln):
                return ln.split("] ", 1)[-1]
        if time.monotonic() >= end:
            return None
        time.sleep(0.05)


def send_raw(run: Run, line: str) -> None:
    try:
        fd = os.open(run.fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        if e.errno in (errno.ENXIO, errno.ENOENT):
            raise NotRunning(f"ctl: {run.name} is not running (no reader on {run.fifo})") from None
        raise
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def send(run: Run, args: list[str], timeout: float = 10.0) -> str:
    """One command and its answer line. Paths become absolute: the runtime's working directory is not ours."""
    args = list(args)
    if args[:1] in (["shot"], ["screen"], ["dump"]) and len(args) > 1:
        args[1] = str(Path(args[1]).absolute())
    if args[:1] == ["mem"] and len(args) > 3:
        args[3] = str(Path(args[3]).absolute())
    before = log_size(run)
    send_raw(run, " ".join(args))
    ans = wait_line(run, before, ANSWER, timeout)
    if ans is None:
        return "ctl: no answer (is the game past its boot?)"
    if args[0] in ("shot", "screen") and len(args) > 1:
        return ans + "\n" + wait_shot(Path(args[1]))
    if args[0] == "dump":
        done = wait_line(run, before, re.compile(r"vk_dump: frame \d+: "), 60)
        return ans + "\n" + (done or "ctl: timed out waiting for the dump")
    return ans


def wait_shot(path: Path, timeout: float = 60) -> str:
    end = time.monotonic() + timeout
    while not (path.exists() and path.stat().st_size):
        if time.monotonic() >= end:
            return f"ctl: timed out waiting for {path}"
        time.sleep(0.1)
    small = path.with_name(path.stem + "-s.png")   # 1280 wide to look at: the shot is the output's size
    if not shutil.which("ffmpeg"):   # GitHub's runners have none: the shot stays at its size
        return f"ctl: wrote {path}"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", "scale=1280:-2", str(small)],
                       capture_output=True)
    return f"ctl: wrote {path}" + (f" (to look at: {small})" if r.returncode == 0 else "")


def frame(run: Run) -> int | None:
    try:
        ans = send(run, ["frame"], timeout=5)
    except NotRunning:
        return None
    m = re.search(r"ctl: frame (\d+)", ans)
    return int(m[1]) if m else None


def run_pids(run: Run) -> list[int]:
    """Processes writing the run's log: the run itself, whatever started it (perf, timeout, a shell)."""
    target = str(run.log.resolve())
    pids = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit() and int(p.name) != os.getpid():
            try:
                if os.readlink(p / "fd/1") == target:
                    pids.append(int(p.name))
            except OSError:
                continue
    return pids


def units(run: Run) -> list[str]:
    r = subprocess.run(["systemctl", "--user", "list-units", "--no-legend", "--plain", "--state=active",
                        f"job-{run.name}.service", f"job-{run.name}-*.scope"], capture_output=True, text=True)
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []


def stop(run: Run) -> str:
    run.stopped.parent.mkdir(parents=True, exist_ok=True)
    run.stopped.write_text(time.strftime("%F %T\n"))
    if us := units(run):
        subprocess.run(["systemctl", "--user", "stop", *us], check=False)
        return f"ctl: {run.name} stopped ({' '.join(us)})"
    pids = run_pids(run)
    if not pids:
        run.stopped.unlink()
        raise NotRunning(f"ctl: {run.name} is not running")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return f"ctl: {run.name} stopped (pid {' '.join(map(str, pids))})"

"""Starting a game run: the defaults of each kind (race, tt, gp, live), the checks done once before it, the run
under its job (memory cap) and perf, the stop once an A/B is measured well enough, and NAME.json at the end.

Each default here carries the reason it had in the shell scripts it replaces (race.sh, run_perf.sh, live.sh).
"""

from __future__ import annotations

import os
import shutil
import signal as sig
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import ctl, report
from paths import ROOT, Run

import abstat

MIN_AVAIL = 8500   # MB: below it the system OOM killed runs and the desktop (integrated GPU, 07/11/2026)


def default(env: dict[str, str], key: str, value: str, *, if_empty: bool = True) -> None:
    """Shell ${KEY:-value} (if_empty) or ${KEY-value} (only when unset)."""
    if key not in env or (if_empty and not env[key]):
        env[key] = value


def race_env(env: dict[str, str], kind: str) -> None:
    if kind in ("tt", "gp"):
        # tt: one kart, at the grid 28-30 s after boot; gp: 24 karts, the save's last cup, Smart Steering and
        # Auto-Accelerate from the save. tt is measured from ReadyRace; gp from the end of its course flyover, as a
        # race: ReadyRace comes ~24 s before it and the replay of that stretch is 2 ms against 15-23 driving (gpaa1,
        # gpseq1, 25/11/2026: ReadyRace 42.9 s, WaitIntroMovie 52.7, IsSequenceFlag 66.8). MKW_SEED fixes the course
        # (the map's first cursor follows the clock: tt6a/b loaded the same world tiles).
        env["MKW_QUICK_RACE"] = kind
        if kind == "tt":
            default(env, "MKW_SEQ_UNTIL", "ReadyRace", if_empty=False)
        default(env, "MKW_SEED", "1", if_empty=False)
    else:
        default(env, "MKW_INPUT_SCRIPT", "tools/run/press-race.txt")
    # The phase watch finds the race's start (the first IsSequenceFlag after the cup movie) and goes off there, so the
    # race is measured without its cost; the run ends RACE_SECS later with the MKW_HOST_TIME census of that window.
    default(env, "MKW_SEQ_WATCH", "1")
    default(env, "MKW_SEQ_UNTIL", "-WaitIntroMovie,IsSequenceFlag", if_empty=False)
    default(env, "MKW_SEQ_EXIT", env.get("RACE_SECS") or "20", if_empty=False)
    # The way to the race at turbo speed with the cup movie skipped; the turbo goes off at MKW_SEQ_UNTIL's event.
    default(env, "MKW_TURBO", "until")
    default(env, "MKW_SKIP_MOVIES", "1")
    default(env, "MKW_FLIGHT_AT", "2000,2700,2900,3000")


def common_env(env: dict[str, str], run: Run) -> None:
    # The 720 lines of the 660M: romfs overlay (tools/romfs/resolution.py) and the game output (patches.c).
    default(env, "MKW_ROMFS_PATCHES", "build/romfs-res", if_empty=False)
    default(env, "MKW_RESOLUTION", "720")
    default(env, "MKW_SPARSE_MIB", "768")
    # 4096 MiB: with 3072 both heaps fill and the race's 1280x720 targets are not created (sp-v0, 01/11/2026).
    default(env, "MKW_GPU_MEM_LIMIT", "4096")
    default(env, "MKW_GPU_GUARD", "1")   # pre-submit checks stop the run before a fault reaches the GPU
    default(env, "MKW_FLIGHT", "1")
    default(env, "MKW_FLIGHT_SPIKE_MS", "0")
    default(env, "MKW_FLIGHT_DIR", str(ROOT / f"scratchpad/flight-{run.name}"))
    default(env, "MKW_TRACE", "gfx,present")
    # Our window opens behind, without the focus; the game is told it has it (it pauses otherwise).
    default(env, "MKW_BACKGROUND_WINDOW", "1")
    default(env, "MKW_ASSUME_FOCUS", "1")
    default(env, "MKW_NO_LAUNCHER", "1")   # a prepared player runtime opens the start screen otherwise (seq180d)
    env["MKW_CTL"] = str(run.fifo)
    env["MKW_FRAMES"] = str(run.frames)
    default(env, "MKW_SPIRV_DUMP", str(ROOT / f"scratchpad/missing-{run.name}"))


def is_runtime(path: Path) -> bool:
    """A real runtime (ELF or PE): host checks apply. The tests' fake runtime is a script."""
    try:
        with path.open("rb") as f:
            return f.read(4) in (b"\x7fELF", b"MZ\x90\x00")
    except OSError:
        return False


def avail_mb() -> int:
    kb = 0
    for ln in Path("/proc/meminfo").read_text().splitlines():
        if ln.startswith(("MemAvailable:", "GPUReclaim:")):
            kb += int(ln.split()[1])
    return kb // 1024


def refusals(run: Run, binary: Path, env: dict[str, str]) -> list[str]:
    out = []
    # 07/11/2026: rules alone did not stop 59 runs against 81 replays; a run says what it expects and concludes.
    if env.get("CLAUDECODE") and not env.get("MKW_RUN_WHY"):
        caps = " ".join(sorted(p.name for p in (ROOT / "build/cap").glob("*"))) or "none"
        out.append('a run needs MKW_RUN_WHY="what I expect to see; what I conclude from each result". A back-end '
                   f"change is checked on a capture in seconds: tools/test/replay.sh build/cap/<name> ({caps})")
    if not is_runtime(binary):
        return out
    if binary.resolve() == (ROOT / "build/mkw").resolve():
        # 01/11/2026: three runs measured the previous binary after a compile error went unseen.
        newer = [p for p in (ROOT / "runtime").rglob("*") if p.suffix in (".c", ".h", ".cpp")
                 and p.stat().st_mtime > binary.stat().st_mtime]
        if newer:
            out.append(f"runtime/ is newer than build/mkw ({newer[0].relative_to(ROOT)}): build first")
    if subprocess.run(["pgrep", "-x", "mkw"], capture_output=True).returncode == 0:
        out.append("a build/mkw is already running (one run at a time)")
    floor = int(env.get("MIN_AVAIL") or MIN_AVAIL)
    if floor < MIN_AVAIL:
        out.append(f"MIN_AVAIL={floor} refused: the floor is {MIN_AVAIL} MB; wait for memory instead (WAIT_MEM)")
    if subprocess.run([str(ROOT / "tools/run/gpu_ok.sh")]).returncode != 0:
        out.append("the GPU is in a state where the next fault can freeze the desktop (gpu_ok.sh)")
    if not out:
        end = time.monotonic() + float(env.get("WAIT_MEM") or 0) * 60
        said = False
        while (mb := avail_mb()) < floor and time.monotonic() < end:
            if not said:   # a silent wait reads as a hung run
                print(f"run: {run.name} waits for memory: {mb} MB available, {floor} needed, up to "
                      f"{env['WAIT_MEM']} min", flush=True)
                said = True
            time.sleep(10)
        if mb < floor:
            out.append(f"only {mb} MB available (with the GPU page pool), below {floor}: WAIT_MEM=<min> waits")
    return out


@dataclass
class Plan:
    run: Run
    kind: str                 # race, tt, gp, live
    seconds: int              # wall-clock kill
    env: dict[str, str]
    binary: Path
    perf: bool = True
    target_ms: float | None = None
    settle: int = 5
    say: object = print
    stop_note: list[str] = field(default_factory=list)


def make_plan(name: str, kind: str, seconds: int | None, env: dict[str, str] | None = None) -> Plan:
    env = dict(os.environ if env is None else env)
    run = Run(name)
    if kind in ("race", "tt", "gp"):
        race_env(env, kind)
    common_env(env, run)
    binary = Path(env.get("MKW_RUN_BIN") or env.get("LIVE_BIN") or ROOT / "build/mkw")
    race_secs = float(env.get("MKW_SEQ_EXIT") or 0)
    # The world clock starts with the race: --seconds must not end it before MKW_SEQ_EXIT (01/11/2026).
    default(env, "WORLD_SECS", str(int(race_secs) + 30))
    if seconds is None:   # the race runs at ~0.5x on the 660M (mem5, 24/11/2026: killed in its first race at 240)
        seconds = {"live": 900, "plain": 120}.get(kind) or max(240, int(env["WORLD_SECS"]) * 2 + 60)
    target = env.get("MKW_AB_TARGET")
    return Plan(run, kind, seconds, env, binary, perf=env.get("RUN_PERF", "1") != "0" and kind != "live",
                target_ms=float(target) if target else None)


# The stop's least pairs (MKW_AB_MIN_PAIRS): the 95 % interval stands on the pairs' spread, and from three pairs that
# spread is a guess (gpab1, 25/11/2026: stopped at 3 pairs ±0.53 ms, 6 s into a race whose first 17 s were its intro)
STOP_MIN_PAIRS = 10


def ab_stop(plan: Plan, done: threading.Event, every: float = 5.0) -> None:
    """Sends `ctl exit` once the A/B's 95 % half-width is at most MKW_AB_TARGET ms on MKW_AB_METRIC (interval, or
    replay, a wait: abstat.METRICS); MKW_SEQ_EXIT stays the limit."""
    metric = plan.env.get("MKW_AB_METRIC") or "interval"
    least = int(plan.env.get("MKW_AB_MIN_PAIRS") or STOP_MIN_PAIRS)
    while not done.wait(every):
        if not plan.run.frames.exists():
            continue
        v, _ = abstat.analyse([plan.run.frames], plan.settle, plan.target_ms, metric)
        if v.half_ms is not None and plan.target_ms is not None and v.half_ms <= plan.target_ms and v.pairs >= least:
            plan.stop_note.append(f"MKW_AB_TARGET {plan.target_ms} ms on {metric} reached at {v.pairs} pairs"
                                  f" (±{v.half_ms:.2f})")
            try:
                ctl.send(plan.run, ["exit"], timeout=5)
            except ctl.NotRunning:
                pass
            return


def prepare(plan: Plan) -> dict:
    run = plan.run
    run.dir.mkdir(parents=True, exist_ok=True)
    for p in (run.json, run.frames, run.stopped, run.fifo):
        p.unlink(missing_ok=True)
    os.mkfifo(run.fifo)
    if is_runtime(plan.binary):   # the KWin rule that keeps our window minimized and without focus
        subprocess.run([str(ROOT / "tools/run/kwin-rule.sh"), "--ensure"], capture_output=True, check=False)
    why = plan.env.get("MKW_RUN_WHY")
    if why:
        with (run.dir / "why.log").open("a") as f:
            f.write(f"{time.strftime('%F %T')}\t{run.name}\t{why}\n")
    now = time.time()
    return {"name": run.name, "kind": plan.kind, "why": why, "binary": report.binary(plan.binary),
            "env": report.mkw_env(plan.env), "seconds": plan.seconds,
            "started": time.strftime("%F %T", time.localtime(now)), "started_unix": now}


def command(plan: Plan) -> list[str]:
    run = plan.run
    game = ["timeout", "-s", "KILL", str(plan.seconds), str(plan.binary), "--seconds", plan.env["WORLD_SECS"]]
    if plan.perf and shutil.which("perf"):
        game = ["perf", "record", *plan.env.get("PERF_ARGS", "").split(), "-F", "499",
                "-o", str(ROOT / f"scratchpad/{run.name}.perf"), "--", *game]
    return [str(ROOT / "tools/run/job.sh"), "run", run.name, *game]


def race(plan: Plan) -> dict:
    run, say = plan.run, plan.say
    no = refusals(run, plan.binary, plan.env)
    if no:
        run.dir.mkdir(parents=True, exist_ok=True)
        meta = {"name": run.name, "kind": plan.kind, "why": plan.env.get("MKW_RUN_WHY"), "refused": no}
        doc = report.write(run, meta, outcome="refused")
        for r in no:
            say(f"run: refused: {r}")
        return doc
    meta = prepare(plan)
    report.write(run, meta, outcome="running")
    plan.env.setdefault("JOB_MEM", "7G")
    say(f"run: {run.name} started ({plan.kind}, limit {plan.seconds} s); log {run.log}")
    done = threading.Event()
    every = float(plan.env.get("MKW_AB_CHECK_S") or 5)
    stopper = threading.Thread(target=ab_stop, args=(plan, done, every), daemon=True)
    if plan.target_ms and plan.env.get("MKW_AB"):
        stopper.start()
    t0 = time.monotonic()
    prev = sig.signal(sig.SIGTERM, _raise_terminated)
    try:
        with run.log.open("wb") as log:
            rc = subprocess.run(command(plan), stdout=log, stderr=subprocess.STDOUT, env=plan.env, cwd=ROOT).returncode
    except Terminated:
        # an outer `timeout` or kill: subprocess.run has killed its child; the game itself goes too, and the JSON
        # still gets its end (gpseq1, 25/11/2026: left "running" by `timeout 200`)
        rc = -15
        plan.stop_note.append("run.py got SIGTERM (an outer timeout or kill)")
        try:
            ctl.stop(run)
        except ctl.NotRunning:
            run.stopped.write_text(time.strftime("%F %T\n"))
    finally:
        sig.signal(sig.SIGTERM, prev)
    elapsed = time.monotonic() - t0
    done.set()
    if stopper.is_alive():   # its stop note goes in the JSON
        stopper.join(10)
    signal = 9 if rc in (-9, 137) else None
    meta["stop"] = "; ".join(plan.stop_note) or None
    # perf and timeout pass the kill on: SIGKILL shows as -9 or 137 either way (checked 25/11/2026)
    doc = report.write(run, meta, rc=rc, signal=signal, timed_out=elapsed >= plan.seconds - 1, ended=time.time())
    run.fifo.unlink(missing_ok=True)
    if plan.perf and (ROOT / f"scratchpad/{run.name}.perf").exists() and "-g" not in plan.env.get("PERF_ARGS", "") \
            and "--call-graph" not in plan.env.get("PERF_ARGS", ""):
        top = run.dir / f"{run.name}.perf.txt"
        r = subprocess.run(["perf", "report", "-i", str(ROOT / f"scratchpad/{run.name}.perf"), "--stdio",
                            "--no-children", "--sort", "symbol"], capture_output=True, text=True)
        top.write_text("\n".join(ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")) + "\n")
    if doc["outcome"] in CLEAN:
        prune_flight(Path(plan.env.get("MKW_FLIGHT_DIR", "")))
    for ln in report.lines(doc):
        say(ln)
    return doc


class Terminated(Exception):
    pass


def _raise_terminated(signum: int, frame: object) -> None:
    raise Terminated


CLEAN = ("exit", "window_closed", "stopped")
FLIGHT_KEEP_EVERY = 60


def prune_flight(root: Path) -> None:
    """A clean run's flight dumps keep their frames.tsv and one thumbnail a second: the exit dump's 600 thumbnails
    were ~350 MiB a run (19 runs, ~6 GiB in scratchpad/ by 25/11/2026); a failed run keeps them all."""
    if not root.name or not root.is_dir():
        return
    for png in root.glob("*/f*.png"):
        try:
            frame = int(png.stem[1:])
        except ValueError:
            continue
        if frame % FLIGHT_KEEP_EVERY:
            png.unlink()


def live(plan: Plan) -> dict:
    run, say = plan.run, plan.say
    no = refusals(run, plan.binary, plan.env)
    if no:
        run.dir.mkdir(parents=True, exist_ok=True)
        for r in no:
            say(f"run: refused: {r}")
        return report.write(run, {"name": run.name, "kind": "live", "refused": no}, outcome="refused")
    meta = prepare(plan)
    if plan.env.get("MKW_SEQ_UNTIL"):   # the way to that scene at turbo speed, off there (core/hacks/turbo.c)
        default(plan.env, "MKW_TURBO", "until")
    if plan.env.get("LIVE_LIB"):
        plan.env["LD_LIBRARY_PATH"] = str(Path(plan.env["LIVE_LIB"]).resolve())
    plan.env.setdefault("JOB_MEM", "7G")
    report.write(run, meta, outcome="running")
    # job start runs a systemd service, which does not inherit this environment: it goes through env.
    keep = {k: v for k, v in plan.env.items()
            if k.startswith(("MKW_", "SDL_", "ENABLE_GAMESCOPE_WSI", "LD_LIBRARY_PATH"))}
    wrap = plan.env.get("LIVE_WRAP", "")
    inner = f"timeout -s KILL {plan.seconds} {wrap} {plan.binary} > {run.log} 2>&1"
    subprocess.run([str(ROOT / "tools/run/job.sh"), "start", run.name, "env",
                    *[f"{k}={v}" for k, v in keep.items()], "bash", "-c", inner], env=plan.env, cwd=ROOT, check=True)
    say(f"run: {run.name} up (live, limit {plan.seconds} s); commands: run.py ctl {run.name} frame|shot PATH|"
        f"dump DIR|flight|press <ticks> <button>|exit|stop; summary: run.py summary {run.name}")
    return meta

"""tools/runner/run.py against the fake runtime (fake_mkw.py): each way a run ends gets its word in NAME.json, an A/B
stops once its interval is narrow enough, and ctl, at and watch do what the shell scripts did, in seconds.
Why: the shell versions had no tests and broke one run at a time (a wait that never ended, a stop that left the run
up, a phase name that never came); one run in seven to 25/11/2026 left no end line to tell why it ended.

Usage: uv run pytest tests/run
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
import run as runpy
import at, ctl, launch, logread, watch
from paths import Run

ROOT = Path(__file__).resolve().parents[2]


def fake(monkeypatch: pytest.MonkeyPatch, **cfg) -> None:
    monkeypatch.setenv("FAKE_MKW", json.dumps(cfg))


def race(name: str, *args: str) -> dict:
    assert runpy.main(["race", name, *args]) in (0, 1)
    return json.loads(Run(name).json.read_text())


def test_race_exits_with_its_phases(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RACE_SECS", "2")
    doc = race("r1")
    assert doc["outcome"] == "exit" and "MKW_SEQ_EXIT" in doc["end_line"]
    assert doc["race_start"] == {"node": "IsSequenceFlag", "frame": 60}
    assert doc["phases"][:3] == ["TitleMenuMain", "WaitIntroMovie", "IsSequenceFlag"]
    assert doc["frames"]["race"]["world_s"] >= 1.9 and 30 < doc["frames"]["race"]["median_ms"] < 37
    assert doc["env"]["MKW_SEQ_EXIT"] == "2" and doc["env"]["MKW_FRAMES"].endswith("r1.frames.tsv")
    assert not Run("r1").fifo.exists()


@pytest.mark.parametrize(("cfg", "word"), [
    ({"fatal_at": 20}, "fatal"),
    ({"crash_at": 20}, "crashed"),
    ({"close_at": 20}, "window_closed"),
])
def test_each_end_has_its_word(runenv: Path, monkeypatch: pytest.MonkeyPatch, cfg: dict, word: str) -> None:
    fake(monkeypatch, **cfg)
    assert race("r2")["outcome"] == word


def test_timeout(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, hang_at=20)
    doc = race("r3", "2")
    assert doc["outcome"] == "killed_timeout" and doc["last_frame"] < 20


def test_killed_before_its_limit_reads_as_oom(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, hang_at=20)
    killer = threading.Thread(target=lambda: (time.sleep(1.5), [os.kill(p, 9) for p in ctl.run_pids(Run("r4"))
                                                               if b"fake_mkw" in Path(f"/proc/{p}/cmdline").read_bytes()]))
    killer.start()
    doc = race("r4", "30")
    killer.join()
    assert doc["outcome"] == "killed_oom", doc


def test_refused_without_a_reason_under_an_agent(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert runpy.main(["race", "r5"]) == 1
    doc = json.loads(Run("r5").json.read_text())
    assert doc["outcome"] == "refused" and "MKW_RUN_WHY" in doc["refused"][0]


def test_reason_goes_to_why_log(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("MKW_RUN_WHY", "expect exit; conclude nothing")
    monkeypatch.setenv("RACE_SECS", "1")
    assert race("r6")["why"] == "expect exit; conclude nothing"
    assert "r6\texpect exit" in (runenv / "why.log").read_text()


def test_ab_stops_at_its_target(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, effect_ms=4.0, fps=2000)
    monkeypatch.setenv("MKW_AB", "0.5")
    monkeypatch.setenv("MKW_AB_TARGET", "1.5")
    monkeypatch.setenv("MKW_AB_CHECK_S", "0.2")
    monkeypatch.setenv("RACE_SECS", "600")
    doc = race("r7")
    assert doc["outcome"] == "exit" and "ctl exit" in doc["end_line"]
    assert "MKW_AB_TARGET" in doc["stop"]
    assert doc["ab"]["significant"] and doc["ab"]["half_ms"] <= 1.5
    assert doc["frames"]["race"]["world_s"] < 600


def test_ab_without_target_runs_to_its_end(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, fps=2000)
    monkeypatch.setenv("MKW_AB", "0.5")
    monkeypatch.setenv("RACE_SECS", "6")
    doc = race("r8")
    assert doc["stop"] is None and doc["ab"]["pairs"] >= 5


def start_live(name: str, monkeypatch: pytest.MonkeyPatch, **cfg) -> Run:
    fake(monkeypatch, **cfg)
    assert runpy.main(["live", name, "60"]) == 0
    run = Run(name)
    end = time.monotonic() + 10
    while "ctl: reading" not in (run.log.read_text() if run.log.exists() else ""):
        assert time.monotonic() < end, "the fake runtime did not start"
        time.sleep(0.05)
    return run


def test_live_ctl_and_stop(runenv: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    run = start_live("l1", monkeypatch, fps=100)
    assert "ctl: frame" in ctl.send(run, ["frame"])
    shot = runenv / "s.png"
    assert "queued" in ctl.send(run, ["screen", str(shot)]) and shot.exists()
    assert "stopped" in ctl.stop(run)
    time.sleep(0.5)
    assert runpy.main(["summary", "l1"]) == 0
    assert json.loads(run.json.read_text())["outcome"] == "stopped"
    with pytest.raises(ctl.NotRunning):
        ctl.send(run, ["frame"])


def test_at_runs_its_commands_after_the_phase(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = start_live("l2", monkeypatch, fps=200, race_frame=150)
    said: list[str] = []
    at.at(run, "WaitIntroMovie,IsSequenceFlag", [0, 20], ["frame"], say=said.append)
    ctl.stop(run)
    assert said[0] == "at: IsSequenceFlag entered at frame 150"
    frames = [int(s.split()[-1]) for s in said[1:]]
    assert len(frames) == 2 and frames[0] >= 150 and frames[1] >= 170


def test_at_refuses_a_bare_leave() -> None:
    with pytest.raises(at.AtError, match="PREV,NODE"):
        at.parse_trigger("-WaitIntroMovie")


def test_at_fails_when_the_run_ends_first(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = start_live("l3", monkeypatch, fps=200)
    ctl.stop(run)
    with pytest.raises(at.AtError, match="ended before"):
        at.at(run, "NeverNode", [0], ["frame"], say=lambda s: None)


def test_watch_stalled_then_end(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = start_live("l4", monkeypatch, fps=200, hang_at=40)
    said: list[str] = []
    t = watch.Timing(poll=0.1, stall_every=0.3, stall_after=3, black_every=100)
    th = threading.Thread(target=watch.watch, args=(run,), kwargs={
        "timing": t, "say": said.append, "stacks": lambda r: runenv / "stacks.txt"})
    th.start()
    end = time.monotonic() + 10
    while not any("STALLED" in s for s in said) and time.monotonic() < end:
        time.sleep(0.1)
    ctl.stop(run)
    th.join(10)
    assert any("STALLED at frame 40" in s for s in said), said
    assert said[-1].startswith("watch: l4 ended")


def test_watch_black_and_fatal(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = start_live("l5", monkeypatch, fps=200, black=True, fatal_at=900)
    said: list[str] = []
    t = watch.Timing(poll=0.1, stall_every=0.2, stall_after=100, black_every=0.3, black_after=3)
    watch.watch(run, timing=t, say=said.append, lum=lambda p: 0, stacks=lambda r: runenv / "x")
    assert any("BLACK" in s for s in said), said
    assert any("FATAL: fake fault at frame 900" in s for s in said)
    assert said[-1].startswith("watch: l5 ended")


def test_outcome_rules() -> None:
    f = logread.LogFacts()
    assert logread.outcome(f, signal=9, timed_out=True, stopped=False) == "killed_timeout"
    assert logread.outcome(f, signal=9, timed_out=False, stopped=True) == "stopped"
    assert logread.outcome(f, signal=None, timed_out=False, stopped=False) == "crashed"
    f.fatal = ["FATAL: x"]
    f.end_line = "frametime: exit"
    assert logread.outcome(f, signal=None, timed_out=False, stopped=False) == "fatal"


def test_plan_defaults(runenv: Path) -> None:
    p = launch.make_plan("x", "tt", None, {"RACE_SECS": "48"})
    assert p.env["MKW_QUICK_RACE"] == "tt" and p.env["MKW_SEQ_UNTIL"] == "ReadyRace"
    assert p.env["WORLD_SECS"] == "78" and p.seconds == 216 + 60 or p.seconds == 240
    assert launch.make_plan("y", "live", None, {}).seconds == 900
    assert "MKW_SEQ_EXIT" not in launch.make_plan("z", "live", None, {}).env


def test_race_sh_wrapper(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RACE_SECS", "1")
    r = subprocess.run([str(ROOT / "tools/run/race.sh"), "w1"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "w1: exit" in r.stdout


def test_sigterm_still_writes_the_end(runenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An outer `timeout` sends SIGTERM to run.py: the game goes and NAME.json gets an end, not "running"."""
    fake(monkeypatch, fps=100)
    monkeypatch.setenv("RACE_SECS", "600")
    p = subprocess.Popen(["python3", str(ROOT / "tools/runner/run.py"), "race", "t1"], env=os.environ.copy(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = Run("t1").log
    end = time.monotonic() + 10
    while "frames: one row" not in (log.read_text() if log.exists() else ""):
        assert time.monotonic() < end, "the fake runtime did not start"
        time.sleep(0.05)
    p.terminate()
    p.wait(15)
    doc = json.loads(Run("t1").json.read_text())
    assert doc["outcome"] == "stopped" and "SIGTERM" in doc["stop"]


def test_clean_run_prunes_its_flight_dump(tmp_path: Path) -> None:
    dump = tmp_path / "flight" / "20261125-000000-f700"
    dump.mkdir(parents=True)
    (dump / "frames.tsv").write_text("x\n")
    for f in range(100, 701):
        (dump / f"f{f:06d}.png").write_bytes(b"")
    launch.prune_flight(tmp_path / "flight")
    kept = sorted(p.name for p in dump.glob("*.png"))
    assert kept == [f"f{f:06d}.png" for f in range(120, 701, 60)] and (dump / "frames.tsv").exists()


def test_shot_without_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub's runners have no ffmpeg (CI of af706edd): the shot is written, just not shrunk."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"png")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert ctl.wait_shot(shot, timeout=1) == f"ctl: wrote {shot}"

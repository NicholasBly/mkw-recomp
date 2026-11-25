"""NAME.json, a run's summary for agents: what ran (binary, commit, runtime files not committed, MKW_*, the reason),
how it ended (one word of logread.OUTCOMES), its phases, frame times from NAME.frames.tsv and the A/B verdict.

Written at launch (outcome "running") and again at the end; `run.py summary NAME` rewrites it from the files of a run
that ended without its launcher (a live run).
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import time
from pathlib import Path

import logread
from paths import ROOT, Run

import abstat


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def binary(path: Path) -> dict:
    out: dict = {"path": str(path)}
    try:
        out["built"] = time.strftime("%F %T", time.localtime(path.stat().st_mtime))
    except OSError:
        out["built"] = None
    if path.resolve().is_relative_to(ROOT / "build"):
        out["commit"] = git("rev-parse", "--short", "HEAD")
        out["uncommitted"] = [ln[3:] for ln in git("status", "--porcelain", "--", "runtime").splitlines()]
    return out


def mkw_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in sorted(env.items()) if k.startswith("MKW_") and k != "MKW_RUN_WHY"}


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def frame_stats(path: Path) -> dict | None:
    if not path.exists():
        return None
    rows = abstat.read_rows(path)
    out = {}
    for label, sel in (("before_race", [r for r in rows if r["world_ms"] < 0]),
                       ("race", [r for r in rows if r["world_ms"] >= 0])):
        ms = [r["interval_us"] / 1000 for r in sel]
        if ms:
            out[label] = {"frames": len(ms), "median_ms": round(statistics.median(ms), 2),
                          "p95_ms": round(pct(ms, 0.95), 2), "p99_ms": round(pct(ms, 0.99), 2),
                          "world_s": round(sel[-1]["world_ms"] / 1000, 1) if label == "race" else None}
    return out or None


def ab(path: Path, env: dict[str, str]) -> dict | None:
    if not env.get("MKW_AB") or not path.exists():
        return None
    target = float(env["MKW_AB_TARGET"]) if env.get("MKW_AB_TARGET") else None
    metric = env.get("MKW_AB_METRIC") or "interval"
    every = abstat.by_metric([path], target=target)

    def rounded(v: abstat.Verdict) -> dict:
        # NaN (no pair, or a wait that is 0 on side A) is not JSON: null, so a strict reader takes NAME.json
        return {k: (None if isinstance(x, float) and math.isnan(x) else round(x, 3) if isinstance(x, float) else x)
                for k, x in v.__dict__.items()}

    # "metric" is the one that decides (and stops the run); "by_metric" has every column's verdict
    return {"metric": metric, **rounded(every[metric]), "by_metric": {m: rounded(v) for m, v in every.items()}}


def write(run: Run, meta: dict, *, outcome: str | None = None, rc: int | None = None,
          signal: int | None = None, timed_out: bool = False, ended: float | None = None) -> dict:
    """meta: what launch knew (name, mode, why, binary, env, started, seconds). outcome None: read it from the files."""
    facts = logread.read(run.log)
    if outcome is None:
        outcome = logread.outcome(facts, signal=signal, timed_out=timed_out, stopped=run.stopped.exists())
    env = meta.get("env", {})
    doc = {
        **meta,
        "outcome": outcome,
        "outcome_means": logread.OUTCOMES[outcome],
        "end_line": facts.end_line,
        "fatal": facts.fatal,
        "rc": rc,
        "ended": time.strftime("%F %T", time.localtime(ended)) if ended else None,
        "wall_s": round(ended - meta["started_unix"], 1) if ended and "started_unix" in meta else None,
        "last_frame": facts.last_frame,
        "race_start": facts.race_start,
        "phases": [p["node"] for i, p in enumerate(facts.phases) if i == 0 or facts.phases[i - 1]["node"] != p["node"]],
        "frames": frame_stats(run.frames),
        "ab": ab(run.frames, env),
        "files": {"log": str(run.log), "frames": str(run.frames)},
    }
    tmp = run.json.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, run.json)
    return doc


def lines(doc: dict) -> list[str]:
    """The few lines a launcher prints at the end: the rest is in the JSON."""
    out = [f"{doc['name']}: {doc['outcome']} ({doc['outcome_means']}); last frame {doc['last_frame']}"
           + (f", wall {doc['wall_s']} s" if doc.get("wall_s") else "")]
    if doc.get("end_line"):
        out.append(f"  end: {doc['end_line']}")
    for f in doc.get("fatal") or []:
        out.append(f"  fatal: {f}")
    race = (doc.get("frames") or {}).get("race")
    if race:
        out.append(f"  race: {race['frames']} frames over {race['world_s']} s, interval median {race['median_ms']} ms,"
                   f" p95 {race['p95_ms']}, p99 {race['p99_ms']}")
    if doc.get("ab"):
        ab_doc = doc["ab"]
        out.append(f"  A/B ({ab_doc.get('metric', 'interval')}): {ab_doc['text']}")
        for m, v in ab_doc.get("by_metric", {}).items():
            if m != ab_doc.get("metric") and v.get("half_ms"):
                out.append(f"    {m}: {v['text']}")
    if doc.get("stop"):
        out.append(f"  stop: {doc['stop']}")
    out.append(f"  summary: {doc['files']['log'][:-4]}.json")
    return out

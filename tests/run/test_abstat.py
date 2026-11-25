"""The A/B verdict (tools/runner/abstat.py) on synthetic per-frame series with a known answer: an A/A run must give an
interval holding 0 about 95 % of the time, a known effect must come out inside its interval, and a scene that gets
slower over the race (a linear trend) must not read as an effect. Why: the old ab.py printed B - A without an error
and most of its 85 verdicts to 25/11/2026 were noise (projects pruebas/partidas-fiables, inv1).

Usage: uv run pytest tests/run
"""

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/runner"))

import abstat  # noqa: E402

HEADER = "frame\tworld_ms\tab\tinterval_us\treplay_us\tpipe_us\ttex_us\tfence_us\tpace_us\n"


def series(seconds: float, period_s: float = 2.0, base_ms: float = 33.3, effect_ms: float = 0.0,
           trend_ms_per_s: float = 0.0, ar: float = 0.9, noise_ms: float = 3.0, seed: int = 0,
           settle_spike_ms: float = 0.0, invert: bool = False, lead_frames: int = 30, spike_frames: int = 3) -> str:
    """Rows the way frames.c writes them: world clock -1 before the race, then MKW_AB's A B B A blocks. The noise is
    AR(1), as a slow stretch of the race lasts many frames."""
    rng = random.Random(seed)
    rows = [HEADER]
    world = 0.0
    e = 0.0
    frame = 1
    for _ in range(lead_frames):
        rows.append(f"{frame}\t-1.0\t0\t{int(base_ms * 1000)}\t0\t0\t0\t0\t0\n")
        frame += 1
    last_side = None
    since_switch = 0
    while world < seconds * 1000:
        k = int(world // (period_s * 1000)) % 4
        side = int((k in (1, 2)) != invert)
        since_switch = 0 if side != last_side else since_switch + 1
        last_side = side
        e = ar * e + rng.gauss(0, noise_ms * (1 - ar * ar) ** 0.5)
        ms = base_ms + trend_ms_per_s * world / 1000 + effect_ms * side + e
        if since_switch < spike_frames:
            ms += settle_spike_ms
        ms = max(ms, 1.0)
        rows.append(f"{frame}\t{world:.1f}\t{side}\t{int(ms * 1000)}\t0\t0\t0\t0\t0\n")
        world += ms
        frame += 1
    return "".join(rows)


def run(tmp_path: Path, text: str, name: str = "r.frames.tsv", **kw) -> abstat.Verdict:
    p = tmp_path / name
    p.write_text(text)
    return abstat.analyse([p], **kw)[0]


def test_aa_interval_holds_zero_about_95_percent(tmp_path: Path) -> None:
    held = 0
    for seed in range(100):
        v = run(tmp_path, series(60, seed=seed))
        assert v.half_ms is not None
        held += abs(v.diff_ms) <= v.half_ms
    assert 88 <= held <= 100, held


def test_known_effect_found_inside_its_interval(tmp_path: Path) -> None:
    v = run(tmp_path, series(60, effect_ms=3.0, seed=1))
    assert v.significant and v.diff_ms > 0
    assert v.half_ms is not None and abs(v.diff_ms - 3.0) <= v.half_ms
    assert "B slower" in v.text


def test_faster_b(tmp_path: Path) -> None:
    v = run(tmp_path, series(60, effect_ms=-3.0, seed=2))
    assert v.significant and "B faster" in v.text


def test_linear_trend_cancels(tmp_path: Path) -> None:
    # 0.2 ms more per second: 12 ms over the minute, four times the noise; A and B see it alike.
    v = run(tmp_path, series(60, trend_ms_per_s=0.2, seed=3, noise_ms=1.0))
    assert v.half_ms is not None
    assert abs(v.diff_ms) <= v.half_ms, v.text


def test_settle_drops_the_switch_transient(tmp_path: Path) -> None:
    # 20 frames of transient in a ~60-frame block: longer than the trimmed mean cuts by itself
    text = series(60, seed=4, noise_ms=0.5, settle_spike_ms=60.0, spike_frames=20)
    kept = run(tmp_path, text, settle=0)
    dropped = run(tmp_path, text, settle=20)
    assert dropped.a_ms < kept.a_ms - 0.5   # the spike leaves the block means


def test_inverted_twin_pools(tmp_path: Path) -> None:
    a = tmp_path / "a.frames.tsv"
    b = tmp_path / "b.frames.tsv"
    a.write_text(series(30, effect_ms=2.0, seed=5))
    b.write_text(series(30, effect_ms=2.0, seed=6, invert=True))
    one = abstat.analyse([a])[0]
    both = abstat.analyse([a, b])[0]
    assert both.pairs > one.pairs
    assert both.diff_ms > 0


def test_too_few_pairs_gives_no_interval(tmp_path: Path) -> None:
    v = run(tmp_path, series(8, seed=7))   # 4 blocks: 2 pairs
    assert v.pairs == 2 and v.half_ms is None and not v.significant
    assert "too few" in v.text


def test_without_mkw_ab_there_is_no_pair(tmp_path: Path) -> None:
    text = HEADER + "".join(f"{i}\t{i * 33.3:.1f}\t0\t33300\t0\t0\t0\t0\t0\n" for i in range(1, 2000))
    v = run(tmp_path, text)
    assert v.pairs == 0 and "no A/B pair" in v.text


def test_cut_short_last_block_is_dropped(tmp_path: Path) -> None:
    text = series(8.4, seed=8)   # the fifth block lasts 0.4 s of its 2
    p = tmp_path / "r.frames.tsv"
    p.write_text(text)
    bs = abstat.blocks(abstat.read_rows(p), 5)
    assert [b.index for b in bs] == [0, 1, 2, 3]


def test_target_says_how_many_pairs(tmp_path: Path) -> None:
    v = run(tmp_path, series(30, seed=9), target=0.1)
    assert v.pairs_for_target is not None and v.pairs_for_target > v.pairs
    assert "needs ~" in v.text


def test_partial_file_from_a_killed_run(tmp_path: Path) -> None:
    text = series(20, seed=10)
    v = run(tmp_path, text[: len(text) - 17])   # the last row cut mid-line, as a kill leaves it
    assert v.pairs >= 3


def test_metric_picks_its_column(tmp_path: Path) -> None:
    """A change that moves only the replay shows on --metric replay and not on the paced interval."""
    rows = [HEADER]
    for i in range(1200):
        world = i * 50.0
        side = int(int(world // 2000) % 4 in (1, 2))   # MKW_AB's A B B A
        rows.append(f"{i}\t{world:.1f}\t{side}\t50000\t{14000 + 2000 * side + (i * 37 % 500)}\t0\t0\t0\t0\n")
    p = tmp_path / "m.frames.tsv"
    p.write_text("".join(rows))
    rep = abstat.analyse([p], metric="replay")[0]
    assert rep.significant and abs(rep.diff_ms - 2.0) < 0.3
    assert not abstat.analyse([p])[0].significant
    assert set(abstat.by_metric([p])) == set(abstat.METRICS)


def test_summary_json_is_strict(tmp_path: Path) -> None:
    """A column that is 0 on both sides (pipe without its trace) gives no percentage: null in NAME.json, never NaN."""
    import json
    import report

    p = tmp_path / "z.frames.tsv"
    p.write_text(series(30, seed=11))
    doc = report.ab(p, {"MKW_AB": "2", "MKW_AB_METRIC": "replay"})
    json.dumps(doc, allow_nan=False)
    assert doc["by_metric"]["pipe"]["pct"] is None

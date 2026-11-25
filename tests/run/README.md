# tests/run

The game-run layer of [tools/runner](../../tools/runner/README.md), checked without the game.

| File | What it checks |
|---|---|
| `fake_mkw.py` | Not a test: a scripted `build/mkw` (`FAKE_MKW` JSON) that writes the log lines, phases, `frames.tsv` and control answers of the real one, and can die by FATAL, crash, hang or window close |
| `conftest.py` | The `runenv` fixture: a temporary `build/run`, no job runner, no perf, the fake as `MKW_RUN_BIN` |
| `test_run.py` | `run.py` end to end in seconds: each way a run ends gets its word in `NAME.json`, the A/B stop at its target, `live` with `ctl` and `stop`, `at` on phases, `watch` with STALLED, BLACK and FATAL, the `.sh` wrappers, a SIGTERM that still writes the end, the flight dump pruned after a clean run, and a shot without ffmpeg (GitHub's runners) |
| `test_abstat.py` | The A/B verdict of `abstat.py` on synthetic per-frame series with a known answer: an A/A interval holds 0 about 95 % of the time, a known effect lands inside its interval, a linear trend over the race does not read as an effect, a run cut mid-line still reads, `--metric` judges only its column, and `NAME.json`'s verdict is strict JSON (null, never NaN) |

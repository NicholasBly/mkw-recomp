# tools/runner

The game-run layer in Python (stdlib only), tested in [tests/run](../../tests/run/README.md) against a fake `mkw`.
The scripts of [tools/run](../run/README.md) (`race.sh`, `run_perf.sh`, `live.sh`, `ctl.sh`, `at.sh`, `watch.sh`)
are one-line wrappers of `run.py`.

| File | What it does | Usage |
|---|---|---|
| `run.py` | `race` (up to the race, `RACE_SECS` of it; `RACE_TT=1`, `RACE_GP=1`, `--kind plain`), `live`, `ctl`, `at`, `watch`, `summary`. Every run writes `build/run/NAME.frames.tsv` (one row per present) and `NAME.json`: binary (path, commit, uncommitted `runtime/` files), `MKW_*`, the reason, how it ended (`exit`, `window_closed`, `fatal`, `crashed`, `killed_timeout`, `killed_oom`, `stopped`, `refused`, `running`), phases, frame times and the A/B verdict. With `MKW_AB` and `MKW_AB_TARGET=MS` it sends `ctl exit` once the 95 % half-width of `MKW_AB_METRIC` (`interval` by default, or `replay`, a wait) is at most that | `tools/runner/run.py race NAME`; `run.py summary NAME [--json]`; see its header |
| `abstat.py` | The A/B verdict with its 95 % interval from `NAME.frames.tsv` of runs with `MKW_AB` (`runtime/core/diag/frames.c`): one sample per block of world clock (its 20 % trimmed mean, so a stutter does not move it), its first frames left out, B - A over adjacent pairs (a linear trend cancels); several files (a run and its `MKW_AB_INVERT` twin) pool their pairs; `--target MS` says how many pairs that half-width needs; `--metric` picks the column judged and the text output gives every column's verdict (only the chosen one decides: six columns at 95 % give a chance hit). Replaces `tools/run/ab.py`'s reading, which gave no error (of 85 A/B runs to 25/11/2026, 18 past two standard errors) | `uv run tools/runner/abstat.py build/run/NAME.frames.tsv [...] [--target 1] [--metric replay]` |
| `paths.py` | Where a run's files live (`build/run/NAME.{log,ctl,frames.tsv,json,stopped}`; `MKW_RUN_DIR` moves the folder) | module |
| `logread.py` | Facts from a log (phases, last frame, race start, end line, FATAL, window closed) and the fixed list of how a run ended | module |
| `ctl.py` | The control FIFO: a command and its answer, screenshots, dumps, the run's processes and `stop` | module |
| `at.py` | Commands when the game enters a phase (`AIGlobalSeq*` nodes), with frame offsets | module |
| `watch.py` | A Monitor's follower: FATAL, guard stops, STALLED (stacks saved), BLACK, and the run's end with its summary | module |
| `report.py` | `NAME.json`: binary, `MKW_*`, outcome, frame times before and in the race, A/B verdict | module |
| `launch.py` | The run itself: defaults of race, time trial, Grand Prix and live runs, the host checks, the command, the A/B stop | module |

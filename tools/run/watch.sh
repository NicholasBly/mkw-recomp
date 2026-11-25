#!/usr/bin/env bash
# The Monitor of a run: FATAL, guard stops, STALLED, BLACK and its end (tools/runner/run.py watch).
# Usage: watch.sh NAME [PATTERN]
exec python3 "$(dirname "$0")/../runner/run.py" watch "$@"

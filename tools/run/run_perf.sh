#!/usr/bin/env bash
# A run under perf with the race runs' checks and caps but no race defaults: tools/runner/run.py race --kind plain.
# Usage: run_perf.sh NAME [SECONDS]
exec python3 "$(dirname "$0")/../runner/run.py" race --kind plain "$@"

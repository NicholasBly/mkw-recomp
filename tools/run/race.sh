#!/usr/bin/env bash
# A race run (RACE_TT=1: time trial, RACE_GP=1: Grand Prix): tools/runner/run.py race, whose header says the rest.
# Usage: race.sh NAME [SECONDS]
exec python3 "$(dirname "$0")/../runner/run.py" race "$@"

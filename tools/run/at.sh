#!/usr/bin/env bash
# ctl commands when the game enters one of its phases (tools/runner/run.py at).
# Usage: at.sh [--past] NAME [PREV,]NODE [+FRAMES[,...]] COMMAND [ARG...] [-- COMMAND...]
exec python3 "$(dirname "$0")/../runner/run.py" at "$@"

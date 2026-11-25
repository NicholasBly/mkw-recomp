#!/usr/bin/env bash
# A run that stays up for ctl (tools/runner/run.py live; LIVE_BIN, LIVE_LIB, LIVE_WRAP as before).
# Usage: live.sh NAME [SECONDS]
exec python3 "$(dirname "$0")/../runner/run.py" live "$@"

#!/usr/bin/env bash
# One command to a running game and its answer (tools/runner/run.py ctl): frame, shot PATH, screen PATH, dump DIR, flight, heap, press..., exit, stop.
# Usage: ctl.sh NAME COMMAND...
exec python3 "$(dirname "$0")/../runner/run.py" ctl "$@"

#!/usr/bin/env python3
"""A stand-in for build/mkw that the run layer (tools/runner/run.py) cannot tell apart where it matters: it writes the
log lines the tools read (phases, the race start, the end), answers the control FIFO (MKW_CTL: frame, exit, shot,
screen) and writes MKW_FRAMES rows with MKW_AB's blocks, at hundreds of frames per real second.

What it does is scripted in FAKE_MKW (JSON), all optional:
  fps         real frames per second (400)          base_ms   frame interval it reports (33.3)
  race_frame  frame the race starts at (60)         effect_ms what B adds to the interval (0)
  noise_ms    AR(1) noise of the interval (2)       seed      its seed (0)
  fatal_at    frame of a FATAL and exit 1           crash_at  frame of a SIGSEGV with no line
  hang_at     frame the frames stop at (ctl still answers)
  close_at    frame the window is closed at
  black       the screen command writes a black image (else a grey one)
"""

import json
import os
import random
import signal
import sys
import threading
import time
import zlib
import struct

cfg = json.loads(os.environ.get("FAKE_MKW") or "{}")
fps = cfg.get("fps", 400)
base = cfg.get("base_ms", 33.3)
race_frame = cfg.get("race_frame", 60)
rng = random.Random(cfg.get("seed", 0))
state = {"frame": 0, "exit": False}


def log(msg: str) -> None:
    sys.stdout.write(f"[host] {msg}\n")
    sys.stdout.flush()


def png(path: str, v: int) -> None:
    w, h = 4, 4
    raw = b"".join(b"\0" + bytes([v, v, v]) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def ctl_reader(path: str) -> None:
    log(f"ctl: reading commands from {path}")
    while True:
        with open(path) as f:
            for line in f:
                cmd, *args = line.split()
                fr = state["frame"]
                if cmd == "frame":
                    log(f"ctl: frame {fr}")
                elif cmd == "exit":
                    log(f"ctl: exit at the next present (frame {fr})")
                    state["exit"] = True
                elif cmd in ("shot", "screen"):
                    log(f"ctl: {cmd} {args[0]} queued at frame {fr}")
                    png(args[0], 0 if cfg.get("black") else 128)
                else:
                    log(f"ctl: unknown command: {line.strip()}")


def side(world_ms: float) -> int:
    period = float(os.environ.get("MKW_AB") or 0) * 1000
    if not period or world_ms < 0:
        return 0
    k = int(world_ms // period) % 4
    return int((k in (1, 2)) != (os.environ.get("MKW_AB_INVERT", "0") not in ("", "0")))


def main() -> None:
    seconds = float(sys.argv[sys.argv.index("--seconds") + 1]) if "--seconds" in sys.argv else 1e9
    seq_exit = float(os.environ.get("MKW_SEQ_EXIT") or 0)
    if os.environ.get("MKW_CTL"):
        threading.Thread(target=ctl_reader, args=(os.environ["MKW_CTL"],), daemon=True).start()
    out = open(os.environ["MKW_FRAMES"], "w") if os.environ.get("MKW_FRAMES") else None
    if out:
        out.write("frame\tworld_ms\tab\tinterval_us\treplay_us\tpipe_us\ttex_us\tfence_us\tpace_us\n")
        log(f"frames: one row per present to {os.environ['MKW_FRAMES']}")
    phases = {10: "TitleMenuMain", 30: "WaitIntroMovie", race_frame: "IsSequenceFlag"}
    world = -1.0
    e = 0.0
    t0 = time.monotonic()
    frame = 0
    while True:
        if frame == cfg.get("hang_at"):
            while True:
                time.sleep(1)
        frame += 1
        state["frame"] = frame
        if state["exit"]:
            log(f"seq: ctl exit, frame {frame}: exit")
            break
        if frame in phases:
            if frame == race_frame:
                log(f"seq: leave WaitIntroMovie frame {frame} at {time.monotonic() - t0:.1f} s")
            log(f"seq: enter {phases[frame]} frame {frame} at {time.monotonic() - t0:.1f} s")
            if frame == race_frame and os.environ.get("MKW_SEQ_UNTIL"):
                log(f"seq: watch off at the start of IsSequenceFlag frame {frame} (MKW_SEQ_UNTIL)")
                world = 0.0
        if frame == cfg.get("fatal_at"):
            log("FATAL: fake fault at frame %d" % frame)
            sys.stdout.flush()
            os._exit(1)
        if frame == cfg.get("crash_at"):
            os.kill(os.getpid(), signal.SIGSEGV)
        if frame == cfg.get("close_at"):
            log("gfx: window closed; exiting")
            break
        e = 0.9 * e + rng.gauss(0, cfg.get("noise_ms", 2.0) * 0.436)
        ms = max(1.0, base + cfg.get("effect_ms", 0.0) * side(world) + e)
        if out:
            out.write(f"{frame}\t{world:.1f}\t{side(world)}\t{int(ms * 1000)}\t0\t0\t0\t0\t0\n")
            if frame % 60 == 0:
                out.flush()
        if world >= 0:
            world += ms
            if seq_exit and world >= seq_exit * 1000:
                log(f"seq: MKW_SEQ_EXIT: {seq_exit:.1f} s after MKW_SEQ_UNTIL, frame {frame}: exit")
                break
            if world >= seconds * 1000:
                log(f"frametime: exit: {frame} frames in {seconds:.1f} s")
                break
        time.sleep(1 / fps)
    if out:
        out.close()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()

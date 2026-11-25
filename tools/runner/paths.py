"""Where a run's files live: build/run/NAME.{log,ctl,frames.tsv,json}. The root is the repo's unless MKW_RUN_DIR
moves build/run (the tests' temporary folders)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_dir() -> Path:
    return Path(os.environ.get("MKW_RUN_DIR") or ROOT / "build/run")


@dataclass(frozen=True)
class Run:
    name: str
    # fixed when made: a thread still running after MKW_RUN_DIR changes keeps its run's folder
    dir: Path = field(default_factory=run_dir, compare=False)

    @property
    def log(self) -> Path:
        return self.dir / f"{self.name}.log"

    @property
    def fifo(self) -> Path:
        return self.dir / f"{self.name}.ctl"

    @property
    def frames(self) -> Path:
        return self.dir / f"{self.name}.frames.tsv"

    @property
    def json(self) -> Path:
        return self.dir / f"{self.name}.json"

    @property
    def stopped(self) -> Path:
        """Left by `run.py ctl NAME stop`: the run's end was asked for, not a crash or the OOM killer."""
        return self.dir / f"{self.name}.stopped"

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .state import Run


@dataclass
class Ctx:
    cfg: Config
    run: Run
    task: dict
    stage: dict
    ws: Path
    attempt: int = 1
    drill: bool = False
    evidence: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    retry_blocking: list[dict] = field(default_factory=list)
    build_log: str = ""

    @property
    def task_id(self) -> str:
        return self.task["id"]

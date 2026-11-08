from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Run:
    """一次流水线运行的全部落盘状态：state.json 是快照，events.jsonl 是唯一事实来源。"""

    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.dir = root / "runs" / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workspace = self.dir / "workspace"
        self.state_path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {
                "run_id": self.run_id,
                "status": "running",
                "stage": None,
                "attempts": {},
                "verdicts": {},
                "created_at": now(),
            }
            self.save()

    def save(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def log(self, event: str, stage: str | None = None, **data) -> None:
        record = {"ts": now(), "event": event}
        if stage:
            record["stage"] = stage
        record.update(data)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stage_dir(self, stage_id: str) -> Path:
        path = self.dir / stage_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact(self, stage_id: str, name: str) -> Path:
        return self.stage_dir(stage_id) / name

    @staticmethod
    def digest(path: Path) -> str:
        if not path.exists():
            return "-"
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

    def write_artifact(self, stage_id: str, name: str, text: str) -> Path:
        path = self.artifact(stage_id, name)
        path.write_text(text, encoding="utf-8", newline="\n")
        self.log(
            "artifact",
            stage_id,
            name=name,
            bytes=path.stat().st_size,
            sha256=self.digest(path),
        )
        return path

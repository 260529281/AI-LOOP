from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _sanitize_no_proxy() -> None:
    """httpx 把 NO_PROXY 的每一项当 URL 解析，[::1] / ::1 会抛 Invalid port。"""
    for name in ("NO_PROXY", "no_proxy"):
        value = os.getenv(name)
        if not value:
            continue
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        keep = [t for t in tokens if "[" not in t and "]" not in t and t != "::1"]
        if len(keep) != len(tokens):
            os.environ[name] = ",".join(keep)


def load_local_env(path: str | Path | None = None) -> None:
    """加载 config/local.env 的 KEY=VALUE；已存在的环境变量优先。"""
    env_path = Path(path) if path else ROOT / "config" / "local.env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    _sanitize_no_proxy()


load_local_env()


class Config:
    def __init__(self) -> None:
        agents = load_yaml(ROOT / "config" / "agents.yaml")["agents"]
        wf = load_yaml(ROOT / "config" / "workflow.yaml")
        self.agents: dict = agents
        self.project: dict = wf["project"]
        self.gates: dict = wf["gates"]
        self.stages: list[dict] = wf["stages"]

    def stage(self, stage_id: str) -> dict:
        for stage in self.stages:
            if stage["id"] == stage_id:
                return stage
        raise KeyError(stage_id)

    @property
    def backend(self) -> str:
        """stub = 确定性桩；cloud = 走 AI_LOOP_MODEL_NAME 指向的 OpenAI 兼容端点。"""
        mode = os.getenv("AI_LOOP_BACKEND", "stub").strip().lower()
        if mode not in {"stub", "cloud"}:
            raise SystemExit(f"AI_LOOP_BACKEND 只能是 stub 或 cloud，收到 {mode!r}")
        return mode

    @property
    def is_stub(self) -> bool:
        return self.backend == "stub"

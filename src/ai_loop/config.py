from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

MECHANICAL_CRITERIA = [
    {"id": "C1", "text": "改动只落在允许的路径内", "check": {"type": "evidence_true", "key": "scope_ok"}},
    {"id": "C2", "text": "补丁能干净应用到项目副本", "check": {"type": "evidence_true", "key": "patch_applied"}},
    {"id": "C3", "text": "assembleDebug 通过", "check": {"type": "evidence_true", "key": "build_ok"}},
]


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

    def stages_for(self, task: dict) -> list[dict]:
        """验收归任务卡：workflow 只定义流程机制，s2 的标准/探针/允许路径由卡自带。

        卡没写 criteria 时按机械三条 + 关键词 + 探针 + 逐条语义验收推导；
        语义验收在确定性模式（桩/外部校验）标未机检，闭环跑交给审批官判。
        """
        impl = task.get("implement") or {}
        out: list[dict] = []
        s2_criteria: list[dict] | None = None
        for raw in self.stages:
            stage = copy.deepcopy(raw)
            if stage["id"] == "s2-implement":
                stage["patch_scope"] = (
                    impl.get("patch_scope") or task.get("patch_paths") or stage.get("patch_scope") or []
                )
                if impl.get("source_context"):
                    stage["source_context"] = impl["source_context"]
                specs = {p["id"]: p for p in impl.get("probes") or []}
                names = list((stage.get("behavior") or {}).get("probes") or []) + list(specs)
                stage["behavior"] = {"probes": names, "specs": specs}
                criteria = impl.get("criteria")
                if criteria is None:
                    criteria = copy.deepcopy(MECHANICAL_CRITERIA)
                    if impl.get("keywords"):
                        criteria.append(
                            {
                                "id": "K1",
                                "text": "补丁改到的文件里包含约定的机检关键词",
                                "check": {"type": "patch_contains_all", "needles": impl["keywords"]},
                            }
                        )
                    for pid in specs:
                        criteria.append(
                            {"id": pid, "text": specs[pid]["text"], "check": {"type": "behavior", "probe": pid}}
                        )
                    for i, line in enumerate(task.get("acceptance") or [], 1):
                        criteria.append({"id": f"A{i}", "text": str(line), "check": {"type": "semantic"}})
                stage["criteria"] = criteria
                s2_criteria = criteria
            if stage["id"] == "s4-accept" and s2_criteria is not None:
                for item in stage.get("criteria", []):
                    check = item.get("check", {})
                    if check.get("type") == "artifact_contains_all" and "PASS" in check.get("needles", []):
                        check["needles"] = ["PASS"] + [c["id"] for c in s2_criteria]
            out.append(stage)
        return out

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

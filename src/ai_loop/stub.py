"""确定性桩模型：产物内容来自 fixture，但审批裁决一律由证据推导，不含固定结论。"""

from __future__ import annotations

from pathlib import Path

from crewai.llms.base_llm import BaseLLM

from . import sandbox
from .config import ROOT
from .context import Ctx


def fixture_dir(task_id: str) -> Path:
    return ROOT / "fixtures" / task_id


def _spec(ctx: Ctx) -> str:
    return (fixture_dir(ctx.task_id) / "spec.md").read_text(encoding="utf-8")


def _patch(ctx: Ctx) -> str:
    name = "implement_bad.patch" if ctx.drill and ctx.attempt == 1 else "implement.patch"
    return (fixture_dir(ctx.task_id) / name).read_text(encoding="utf-8")


def _s2_evidence(ctx: Ctx) -> dict:
    return (ctx.history.get("s2-implement") or {}).get("evidence", {})


def _review(ctx: Ctx) -> str:
    patch = ctx.run.artifact("s2-implement", "change.patch")
    stats = sandbox.patch_stats(patch) if patch.exists() else {"added": 0, "removed": 0, "files": []}
    evidence = _s2_evidence(ctx)
    build_ok = evidence.get("build_ok")
    build_line = (
        f"证据：`gradle --offline assembleDebug` 退出码 {evidence.get('build_returncode', '-')}（见 `s2-implement/build-*.log`）"
        if build_ok
        else f"证据：构建未通过，退出码 {evidence.get('build_returncode', '-')}（见 `s2-implement/build-*.log`）"
    )
    blocking = "- 无"
    if not build_ok:
        blocking = f"- [严重] 构建失败，日志尾部：\n```\n{evidence.get('build_tail', '')}\n```"
    return f"""# 审查结论（reviewer）

对象：`s2-implement/change.patch`（+{stats['added']} / -{stats['removed']}，{len(stats['files'])} 个文件）
文件：{", ".join(stats["files"])}
{build_line}
证据：L1 机械校验 patch_scope={evidence.get("scope_ok", "-")}、patch_applies={evidence.get("patch_applied", "-")}

## 阻塞项
{blocking}

## 非阻塞发现
- 白名单被全部取消时，所有心情都回退「平常」，与 spec 的非目标一节一致，不阻塞。
- 勾选写进 SharedPreferences 的 StringSet，重启后由 `readAllowedEmotions()` 读回。
"""


def _acceptance(ctx: Ctx) -> str:
    verdict = ctx.verdicts.get("s2-implement", {})
    rows = []
    for item in verdict.get("criteria", []):
        passed = item.get("passed")
        mark = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
        rows.append(f"| {item['id']} | {item['text']} | {mark} | {item['evidence']} |")
    ok = verdict.get("verdict") == "pass"
    skipped = verdict.get("skipped_semantic") or []
    evidence = _s2_evidence(ctx)
    if ok and skipped:
        summary = f"PASS：机检标准全部通过；语义验收项 {', '.join(skipped)} 未机检，需审批官或人工确认。"
    elif ok:
        summary = "PASS：实现满足需求卡全部验收项。"
    else:
        summary = "FAIL：存在未通过的验收标准，不能交付。"
    return f"""# 验收结论（acceptance）

依据：`s1-decompose/spec.md`、`s2-implement/change.patch`、`s3-review/review.md`
证据：s2 审批官裁决 {verdict.get("verdict", "-")}，构建退出码 {evidence.get("build_returncode", "-")}，审查结论见 `s3-review/review.md`

| 标准 | 内容 | 结论 | 证据 |
|---|---|---|---|
{chr(10).join(rows)}

## 总结
{summary}
"""


def _readback(ctx: Ctx) -> str:
    lines = ["## 上游结论"]
    for rel in ctx.stage.get("consumes", []):
        stage_id, name = rel.split("/", 1)
        path = ctx.run.artifact(stage_id, name)
        head = sandbox.read_text(path).strip().splitlines()[:3]
        summary = " / ".join(line.strip("# ").strip() for line in head if line.strip())
        lines.append(f"- `{rel}`（sha256:{ctx.run.digest(path)}）：{summary[:160]}")
    lines.append("## 依赖契约")
    lines.append("- 我按上游产物里写明的接口与验收标准动手，不自行扩大范围。")
    for rel in ctx.stage.get("consumes", []):
        lines.append(f"- 依赖 `{rel}` 的全部约束条款")
    lines.append("## 疑点")
    lines.append("- 无")
    return "\n".join(lines) + "\n"


EMITTERS = {
    "s1-decompose": _spec,
    "s2-implement": _patch,
    "s3-review": _review,
    "s4-accept": _acceptance,
}


class StubLLM(BaseLLM):
    """把 CrewAI 的 LLM 调用接到确定性产物上，agent/task 机制照常跑。"""

    def __init__(self, emitter) -> None:
        super().__init__(model="stub-deterministic")
        self._emitter = emitter

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None):
        return self._emitter()

    def get_context_window_size(self) -> int:
        return 8192


def stage_emitter(ctx: Ctx):
    if ctx.stage["id"] not in EMITTERS:
        raise KeyError(f"没有为 {ctx.stage['id']} 准备桩产物")
    return lambda: EMITTERS[ctx.stage["id"]](ctx)


def readback_emitter(ctx: Ctx):
    return lambda: _readback(ctx)

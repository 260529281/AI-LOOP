from __future__ import annotations

import difflib
import re
from pathlib import Path

from crewai import Agent, Crew, Process, Task

from . import arbiter, behavior, checks, sandbox, stub
from .config import ROOT, Config, load_yaml
from .context import Ctx
from .llm import build_real_llm
from .report import write_report
from .state import Run

MAX_FILE_CHARS = 60000


class Escalated(Exception):
    def __init__(self, stage: str, blocking: list[dict]) -> None:
        super().__init__(f"{stage} 超出返工预算")
        self.stage = stage
        self.blocking = blocking


def extract_patch(text: str) -> str:
    """模型经常把补丁包在解释或 Markdown 围栏里，只留 git 能吃的部分。"""
    fence = re.search(r"```(?:diff|patch)?[ \t]*\r?\n(.*?)```", text, re.S)
    body = fence.group(1) if fence else text
    start = body.find("diff --git ")
    if start > 0:
        body = body[start:]
    return body.strip() + "\n"


FILE_BLOCK = re.compile(
    r"<<<FILE:\s*(?P<path>[^>\n]+?)\s*>>>\r?\n(?P<body>.*?)<<<END FILE>>>", re.S
)


def patch_from_files(ctx: Ctx, text: str) -> str:
    """模型给完整文件，补丁由我们按基线生成——比让它手数 hunk 行数可靠得多。"""
    blocks = FILE_BLOCK.findall(text)
    if not blocks:
        return extract_patch(text)
    chunks = []
    for rel, body in blocks:
        rel = rel.strip().lstrip("./")
        base = ctx.ws / rel
        old = base.read_text(encoding="utf-8").splitlines(keepends=True) if base.exists() else []
        new = body.splitlines(keepends=True)
        if new and not new[-1].endswith("\n"):
            new[-1] += "\n"
        chunks.append(
            "".join(
                difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3)
            )
        )
    return "".join(chunks).strip() + "\n"


def build_prompt(
    ctx: Ctx,
    contract_override: str | None = None,
    include_source: bool = True,
) -> str:
    stage, task = ctx.stage, ctx.task
    lines = [f"# 任务：{task['title']}", str(task["requirement"]).strip(), ""]

    if task.get("acceptance") and contract_override is None:
        lines.append("## 需求卡验收（行为）")
        lines += [f"- {item}" for item in task["acceptance"]]

    if stage.get("consumes"):
        lines.append("## 上游产物")
        for rel in stage["consumes"]:
            stage_id, name = rel.split("/", 1)
            body = sandbox.read_text(ctx.run.artifact(stage_id, name))[:20000]
            lines += [f"### {rel}", "```", body.strip(), "```"]

    if include_source and stage.get("source_context"):
        lines.append("## 相关源码（只允许改这些文件）")
        for rel in stage["source_context"]:
            body = sandbox.read_text(ctx.ws / rel)[:MAX_FILE_CHARS]
            lines += [f"### {rel}", "```", body.rstrip(), "```"]

    if stage.get("patch_scope"):
        lines.append("## 允许改动的路径")
        lines += [f"- {pattern}" for pattern in stage["patch_scope"]]

    if stage.get("criteria") and contract_override is None:
        lines.append("## 验收标准（必须逐条满足）")
        lines += [f"- {c['id']} {c['text']}" for c in stage["criteria"]]

    evidence = (ctx.history.get("s2-implement") or {}).get("evidence") or {}
    if evidence:
        lines.append("## 构建证据")
        lines.append(
            f"- assembleDebug 退出码：{evidence.get('build_returncode', '-')}；"
            f"日志：s2-implement/build-*.log"
        )
        if evidence.get("build_tail"):
            lines += ["```", evidence["build_tail"], "```"]

    if ctx.retry_blocking:
        lines.append("## 上一轮被否决的原因（必须逐条修掉）")
        for item in ctx.retry_blocking:
            reason = item.get("reason") or item.get("text") or ""
            detail = (item.get("evidence") or "").replace("\n", " ")
            lines.append(f"- [{item.get('criterion')}] {reason}｜证据：{detail[:400]}")

    lines += [
        "## 输出要求",
        contract_override or stage.get("output_contract", "只输出产物正文，不要解释过程。"),
    ]
    return "\n".join(lines)


def _agent_output(cfg: Config, agent_key: str, description: str, expected: str, emitter) -> str:
    """所有产物都经 CrewAI 的 Agent/Task 机制产出，桩模型只是替换了 LLM 实现。"""
    spec = cfg.agents[agent_key]
    llm = stub.StubLLM(emitter) if cfg.is_stub else build_real_llm()
    agent = Agent(
        role=spec["role"],
        goal=spec["goal"],
        backstory=spec["backstory"],
        llm=llm,
        verbose=False,
        allow_delegation=False,
        max_iter=1,
    )
    task = Task(description=description, expected_output=expected, agent=agent)
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        memory=False,
        cache=False,
    )
    try:
        return str(crew.kickoff().raw).strip()
    except Exception as exc:
        raise RuntimeError(f"模型调用失败：{exc}") from exc


def _readback_gate(ctx: Ctx, arbiter_llm=None, max_tries: int = 2) -> None:
    needles = ctx.stage["readback"]
    criteria = [
        {
            "id": "R1",
            "text": f"接手前复述包含 {' / '.join(needles)} 三节",
            "check": {"type": "artifact_contains_all", "artifact": "readback.md", "needles": needles},
        }
    ]
    for attempt in range(1, max_tries + 1):
        ctx.attempt = attempt
        text = _agent_output(
            ctx.cfg,
            ctx.stage["agent"],
            build_prompt(
                ctx,
                contract_override="只输出三段式接手复述：## 上游结论 / ## 依赖契约 / ## 疑点；不要写别的。",
                include_source=False,
            ),
            "三段式 read-back",
            stub.readback_emitter(ctx),
        )
        ctx.run.write_artifact(ctx.stage["id"], "readback.md", text + "\n")
        empty_l1 = {"ok": True, "checks": []}
        if arbiter_llm is None:
            verdict = arbiter.judge(ctx, empty_l1, criteria_list=criteria, label="readback")
        else:
            verdict = arbiter.judge_with_llm(
                ctx,
                empty_l1,
                arbiter_llm,
                criteria_list=criteria,
                label="readback",
                artifact_names=["readback.md"],
            )
        if verdict["verdict"] == "pass":
            return
        ctx.retry_blocking = verdict["blocking"]
        ctx.run.log("readback_revise", ctx.stage["id"], attempt=attempt)
    raise Escalated(ctx.stage["id"], verdict["blocking"])


def verify_patch(task_path: str, patch_text: str, run_id: str | None = None) -> Run:
    """外部补丁校验：补丁由人（或 Qoder）产出，这里只落地、跑机械校验和行为探针，
    审批用确定性 rubric，全程不调用任何模型。"""
    cfg = Config()
    task = load_yaml(task_path)
    run = Run(ROOT, run_id)
    run.state["mode"] = "verify"
    run.state["stage"] = "s2-implement"
    run.save()
    run.log("run_start", task=task["id"], backend="verify", mode="verify")

    stage = next(s for s in cfg.stages_for(task) if s["id"] == "s2-implement")
    run.log("stage_start", stage["id"])
    patch_text = patch_text.strip() + "\n"
    run.write_artifact(stage["id"], "change.patch", patch_text)
    run.log("artifact", stage["id"], name="change.patch", bytes=len(patch_text.encode()))

    project_root = Path(cfg.project["root"])
    ws = sandbox.prepare(project_root, run.workspace, cfg.project["exclude_dirs"])
    ctx = Ctx(cfg=cfg, run=run, task=task, stage=stage, ws=ws, attempt=1)

    l1 = checks.run_l1(ctx)
    if stage.get("behavior"):
        behavior.run_probes(
            ctx, stage["behavior"].get("probes"), specs=stage["behavior"].get("specs")
        )
    verdict = arbiter.judge(ctx, l1)

    run.state["attempts"][stage["id"]] = 1
    run.state["verdicts"][stage["id"]] = verdict["verdict"]
    run.state["status"] = "passed" if verdict["verdict"] == "pass" else "blocked"
    run.state["stage"] = None
    run.save()
    run.log("run_end", status=run.state["status"], mode="verify", skipped_semantic=verdict.get("skipped_semantic", []))
    write_report(run, cfg, task)
    return run


def run_pipeline(task_path: str, drill: bool = False, run_id: str | None = None) -> Run:
    cfg = Config()
    task = load_yaml(task_path)
    run = Run(ROOT, run_id)
    max_revisions = int(cfg.gates.get("max_revisions", 2))
    arbiter_llm = None if cfg.is_stub else build_real_llm()

    run.log("run_start", task=task["id"], drill=drill, backend=cfg.backend)
    project_root = Path(cfg.project["root"])
    ws = sandbox.prepare(project_root, run.workspace, cfg.project["exclude_dirs"])

    verdicts: dict = {}
    history: dict = {}
    for stage in cfg.stages_for(task):
        ctx = Ctx(
            cfg=cfg,
            run=run,
            task=task,
            stage=stage,
            ws=ws,
            drill=drill,
            verdicts=verdicts,
            history=history,
        )
        run.state["stage"] = stage["id"]
        run.save()
        run.log("stage_start", stage["id"])

        if stage.get("readback"):
            _readback_gate(ctx, arbiter_llm)

        for attempt in range(1, max_revisions + 2):
            ctx.attempt = attempt
            if attempt > 1:
                ws = sandbox.prepare(project_root, run.workspace, cfg.project["exclude_dirs"])
                ctx.ws = ws
            ctx.evidence = {}

            text = _agent_output(
                cfg, stage["agent"], build_prompt(ctx), stage["produces"][0], stub.stage_emitter(ctx)
            )
            if stage["produces"][0] == "change.patch":
                text = patch_from_files(ctx, text)
            run.log(
                "agent_message",
                stage["id"],
                attempt=attempt,
                agent=stage["agent"],
                chars=len(text),
                text=text[:1200] + ("\n…（完整内容见产物）" if len(text) > 1200 else ""),
            )
            run.write_artifact(stage["id"], stage["produces"][0], text + "\n")

            l1 = checks.run_l1(ctx)
            if stage.get("behavior"):
                behavior.run_probes(
                    ctx, stage["behavior"].get("probes"), specs=stage["behavior"].get("specs")
                )
            if arbiter_llm is None:
                verdict = arbiter.judge(ctx, l1)
            else:
                # 语义标准交给核心审批官；L1 机械结论仍是不可越过的底线
                verdict = arbiter.judge_with_llm(ctx, l1, arbiter_llm)
            history[stage["id"]] = {
                "evidence": dict(ctx.evidence),
                "checks": l1["checks"],
                "verdict": verdict["verdict"],
                "criteria": verdict["criteria"],
            }
            run.state["attempts"][stage["id"]] = attempt
            if verdict["verdict"] == "pass":
                verdicts[stage["id"]] = verdict
                run.state["verdicts"][stage["id"]] = "pass"
                run.save()
                break

            run.log(
                "revise",
                stage["id"],
                attempt=attempt,
                blocking=[b["criterion"] for b in verdict["blocking"]],
            )
            if attempt >= max_revisions + 1:
                run.state["status"] = "blocked"
                run.save()
                run.log("escalate", stage["id"], attempt=attempt, reason="返工预算用尽")
                write_report(run, cfg, task)
                raise Escalated(stage["id"], verdict["blocking"])
            ctx.retry_blocking = verdict["blocking"]

    run.state["status"] = "passed"
    run.state["stage"] = None
    run.save()
    skipped = sorted({sid for v in verdicts.values() for sid in v.get("skipped_semantic", [])})
    run.log("run_end", status="passed", skipped_semantic=skipped)
    write_report(run, cfg, task)
    return run

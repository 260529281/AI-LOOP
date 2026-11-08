from __future__ import annotations

import json

from .config import Config
from .state import Run


def _verdicts(run: Run, stage_id: str) -> list[dict]:
    out = []
    for path in sorted((run.dir / stage_id).glob("verdict-*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def write_report(run: Run, cfg: Config, task: dict) -> None:
    verify = run.state.get("mode") == "verify"
    model_line = "外部补丁校验（不调用模型）" if verify else f"`{cfg.backend}`"
    lines = [
        f"# 运行报告 · {task['id']}",
        "",
        f"- run_id：`{run.run_id}`",
        f"- 状态：**{run.state['status']}**",
        f"- 模型：{model_line}",
        "",
        "| 环节 | 负责人 | 尝试次数 | 裁决 | 产物 |",
        "|---|---|---|---|---|",
    ]
    for stage in cfg.stages:
        sid = stage["id"]
        verdicts = _verdicts(run, sid)
        stage_verdicts = [v for v in verdicts if v.get("label", "stage") == "stage"]
        last = stage_verdicts[-1]["verdict"] if stage_verdicts else "-"
        artifacts = sorted(p.name for p in (run.dir / sid).glob("*") if p.name != "readback.md")
        lines.append(
            f"| {stage['title']} | {cfg.agents[stage['agent']]['name']} | "
            f"{run.state['attempts'].get(sid, '-')} | {last} | {', '.join(artifacts)} |"
        )

    lines += ["", "## 每次裁决的证据", ""]
    for stage in cfg.stages:
        sid = stage["id"]
        for verdict in _verdicts(run, sid):
            label = verdict.get("label", "stage")
            lines.append(f"### {sid} · {label} · 第 {verdict['attempt']} 次 → {verdict['verdict']}")
            for item in verdict.get("criteria", []):
                passed = item.get("passed")
                mark = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
                lines.append(f"- {mark} `{item.get('id')}` {item.get('text')} — {item.get('evidence')}")
            skipped = verdict.get("skipped_semantic") or []
            if skipped:
                lines.append(f"- 未机检（语义验收，确定性模式不判）：{', '.join(skipped)}，需审批官或人工确认")
            for block in verdict.get("blocking", []):
                source = block.get("source", "L2")
                reason = block.get("reason") or block.get("text") or ""
                evidence = (block.get("evidence") or "").replace("\n", " ")
                lines.append(f"- 阻塞（{source}）{block.get('criterion')}：{reason}｜证据：{evidence[:300]}")
            lines.append("")

    (run.dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    run.log("report", path=str(run.dir / "report.md"))

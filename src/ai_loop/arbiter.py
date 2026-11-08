"""L2 核心审批官。

桩模式：按验收标准逐条评估证据（确定性 rubric），不是「看起来不错」。
真实模型模式：只喂标准 + 产物 + 证据，强制 JSON verdict，解析失败一律不通过。
"""

from __future__ import annotations

import json
import re

from .context import Ctx


def _artifact_text(ctx: Ctx, name: str) -> str:
    path = ctx.run.artifact(ctx.stage["id"], name)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _eval(check: dict, ctx: Ctx) -> tuple[bool, str]:
    kind = check["type"]
    if kind == "artifact_contains_all":
        text = _artifact_text(ctx, check["artifact"])
        missing = [n for n in check["needles"] if n not in text]
        detail = "全部命中" if not missing else f"{check['artifact']} 缺：{', '.join(missing)}"
        return not missing, detail
    if kind == "evidence_true":
        value = ctx.evidence.get(check["key"])
        return value is True, f"{check['key']}={value!r}"
    if kind == "behavior":
        item = (ctx.evidence.get("behavior") or {}).get(check["probe"])
        if not item:
            return False, f"没有探针 {check['probe']} 的结果"
        return bool(item["ok"]), item.get("detail", "")
    raise ValueError(f"未知检查类型：{kind}")


def judge(ctx: Ctx, l1: dict, criteria_list: list[dict] | None = None, label: str | None = None) -> dict:
    criteria = []
    for item in criteria_list if criteria_list is not None else ctx.stage.get("criteria", []):
        ok, detail = _eval(item["check"], ctx)
        criteria.append(
            {"id": item["id"], "text": item["text"], "passed": ok, "evidence": detail}
        )

    blocking = [
        {
            "source": "L2",
            "criterion": item["id"],
            "reason": item["text"],
            "evidence": item["evidence"],
        }
        for item in criteria
        if not item["passed"]
    ]
    blocking += [
        {
            "source": "L1",
            "criterion": check["check"],
            "reason": "机械校验未通过",
            "evidence": check["detail"],
        }
        for check in l1["checks"]
        if not check["ok"]
    ]

    passed = all(item["passed"] for item in criteria) and l1["ok"]
    verdict = {
        "stage": ctx.stage["id"],
        "attempt": ctx.attempt,
        "label": label or "stage",
        "arbiter": "rubric",
        "verdict": "pass" if passed else "revise",
        "criteria": criteria,
        "blocking": blocking,
        "evidence_checked": {
            "l1": [f"{c['check']}={c['ok']}" for c in l1["checks"]],
            "behavior_ok": ctx.evidence.get("behavior_ok", "-"),
            "build_returncode": ctx.evidence.get("build_returncode", "-"),
        },
    }
    suffix = f"{label}-{ctx.attempt}" if label else str(ctx.attempt)
    path = ctx.run.artifact(ctx.stage["id"], f"verdict-{suffix}.json")
    path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.run.log(
        "verdict",
        ctx.stage["id"],
        attempt=ctx.attempt,
        label=label or "stage",
        verdict=verdict["verdict"],
        blocking=[
            {
                "criterion": b["criterion"],
                "reason": b.get("reason", ""),
                "evidence": (b.get("evidence") or "")[:300],
            }
            for b in blocking
        ],
        sha256=ctx.run.digest(path),
    )
    return verdict


ARBITER_PROMPT = """你是核心审批官，只依据下面三样东西裁决，不得补充任何没写出来的结论。

## 验收标准
{criteria}

## 产物
{artifact}

## 机械校验证据
{evidence}

behavior 是行为探针在补丁文件里找到的实现线索，不是唯一写法：补丁用别的方式实现了同一行为也算数，
但结论必须引用补丁里的具体代码，不能只引用探针的布尔值。

只输出 JSON，不要解释：
{{"verdict":"pass|revise","criteria":[{{"id":"C1","passed":true,"evidence":"引用证据原文"}}],
  "blocking":[{{"criterion":"C2","reason":"不满足哪条标准","evidence":"引用证据原文"}}]}}
criteria 必须与上面的标准逐条对应，id 原样照抄，不得增删。
任一标准无法用证据证明即判 revise。
"""


def judge_with_llm(
    ctx: Ctx,
    l1: dict,
    llm,
    criteria_list: list[dict] | None = None,
    label: str | None = None,
    artifact_names: list[str] | None = None,
) -> dict:
    criteria_defs = criteria_list if criteria_list is not None else ctx.stage.get("criteria", [])
    criteria = "\n".join(f"- {c['id']} {c['text']}" for c in criteria_defs)
    artifact = "\n\n".join(
        f"### {name}\n{_artifact_text(ctx, name)}"
        for name in (artifact_names or ctx.stage.get("produces", []))
    )
    evidence = json.dumps(
        {
            "l1": l1["checks"],
            "behavior": ctx.evidence.get("behavior", {}),
            "build_log_tail": ctx.evidence.get("build_tail", ""),
        },
        ensure_ascii=False,
    )
    prompt = ARBITER_PROMPT.format(criteria=criteria, artifact=artifact, evidence=evidence)
    try:
        raw = llm.call([{"role": "user", "content": prompt}])
        data = json.loads(re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
        items = data.get("criteria", [])
        # 模型偶尔会改写标准 id，按条数+逐条结论兜住；少判一条也算不合格
        covered = len(items) >= len(criteria_defs) and all(
            bool(item.get("passed")) for item in items
        )
        passed = (
            data.get("verdict") == "pass"
            and l1["ok"]
            and covered
            and not data.get("blocking")
        )
        verdict = {
            "stage": ctx.stage["id"],
            "attempt": ctx.attempt,
            "label": label or "stage",
            "arbiter": "llm",
            "verdict": "pass" if passed else "revise",
            "criteria": data.get("criteria", []),
            "blocking": list(data.get("blocking") or []),
        }
    except Exception as exc:  # fail-closed：审批官出错就不放行
        verdict = {
            "stage": ctx.stage["id"],
            "attempt": ctx.attempt,
            "label": label or "stage",
            "arbiter": "llm",
            "verdict": "revise",
            "criteria": [],
            "blocking": [
                {
                    "source": "L2",
                    "criterion": "arbiter",
                    "reason": "审批官输出不可用，按不通过处理",
                    "evidence": str(exc)[:300],
                }
            ],
        }
    if verdict["verdict"] != "pass" and not verdict["blocking"]:
        # 审批官只给结论不给理由时，从判负的标准反推阻塞项，返工才有方向
        verdict["blocking"] = [
            {
                "source": "L2",
                "criterion": str(item.get("id")),
                "reason": "审批官判定该标准未被证据满足",
                "evidence": str(item.get("evidence") or "")[:400],
            }
            for item in verdict.get("criteria", [])
            if item.get("passed") is False
        ] or [
            {
                "source": "L2",
                "criterion": "arbiter",
                "reason": "审批官判 revise 但未给出理由",
                "evidence": "见 verdict 原文",
            }
        ]
    # 机械校验是否决底线：审批官说 pass 也不能越过 L1 失败
    verdict["blocking"] += [
        {
            "source": "L1",
            "criterion": check["check"],
            "reason": "机械校验未通过",
            "evidence": check["detail"],
        }
        for check in l1["checks"]
        if not check["ok"]
    ]
    if verdict["blocking"]:
        verdict["verdict"] = "revise"
    verdict["evidence_checked"] = {
        "l1": [f"{c['check']}={c['ok']}" for c in l1["checks"]],
        "build_returncode": ctx.evidence.get("build_returncode", "-"),
    }
    suffix = f"{label}-{ctx.attempt}" if label else str(ctx.attempt)
    path = ctx.run.artifact(ctx.stage["id"], f"verdict-{suffix}.json")
    path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.run.log(
        "verdict",
        ctx.stage["id"],
        attempt=ctx.attempt,
        label=label or "stage",
        arbiter="llm",
        verdict=verdict["verdict"],
        blocking=[
            {
                "criterion": b.get("criterion", ""),
                "reason": b.get("reason", ""),
                "evidence": (b.get("evidence") or "")[:300],
            }
            for b in verdict["blocking"]
        ],
        sha256=ctx.run.digest(path),
    )
    return verdict

"""行为探针：补丁落进副本后，按「行为」而不是「符号名」找证据。

探针只看补丁改到的文件，结果作为证据交给审批官，不单独否决：
同一行为可以有很多写法，换个名字或换个持久化方式都算数，最终由 L2 判断证据够不够。
"""

from __future__ import annotations

import re

from .context import Ctx

# 可复用探针库：任务专属探针写在任务卡的 implement.probes 里，通用探针才放这里。
PROBES: dict = {}


def _changed_files(ctx: Ctx) -> dict[str, str]:
    texts = {}
    for rel in ctx.evidence.get("patch_files", []):
        path = ctx.ws / rel
        if path.is_file():
            texts[rel] = path.read_text(encoding="utf-8", errors="replace")
    return texts


def changed_blob(ctx: Ctx) -> str:
    return "\n".join(_changed_files(ctx).values())


def run_probes(ctx: Ctx, names: list[str] | None = None, specs: dict | None = None) -> dict:
    registry = {**PROBES, **(specs or {})}
    wanted = names if names is not None else list(registry)
    blob = changed_blob(ctx)
    results = {}
    for name in wanted:
        probe = registry.get(name)
        if not probe:
            results[name] = {"ok": False, "text": name, "detail": "没有这个探针"}
            continue
        if "all" in probe:
            hits = [re.search(pattern, blob, re.MULTILINE) for pattern in probe["all"]]
            ok = all(hits)
            detail = (
                "命中全部特征"
                if ok
                else f"只命中 {sum(1 for h in hits if h)}/{len(hits)} 个特征，行为证据不完整"
            )
        else:
            ok = any(re.search(pattern, blob, re.MULTILINE) for pattern in probe["any"])
            detail = "在补丁文件里找到实现" if ok else "补丁文件里没找到这个行为的实现"
        results[name] = {"ok": ok, "text": probe["text"], "detail": detail}

    ctx.evidence["behavior"] = results
    ctx.evidence["behavior_ok"] = all(item["ok"] for item in results.values())
    ctx.run.log(
        "behavior",
        ctx.stage["id"],
        attempt=ctx.attempt,
        ok=ctx.evidence["behavior_ok"],
        results=[{"id": key, **value} for key, value in results.items()],
    )
    return results

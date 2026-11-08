"""行为探针：补丁落进副本后，按「行为」而不是「符号名」找证据。

探针只看补丁改到的文件，结果作为证据交给审批官，不单独否决：
同一行为可以有很多写法，换个名字或换个持久化方式都算数，最终由 L2 判断证据够不够。
"""

from __future__ import annotations

import re

from .context import Ctx

PROBES = {
    "default_all": {
        "text": "默认全开：白名单初值来自表情全集",
        "any": [
            r"Emotion\.(?:entries|values\(\))[^\n]{0,60}to(?:Set|MutableSet)\(\)",
            r"EnumSet\.allOf\(\s*Emotion",
            r"Emotion\.(?:entries|values\(\))[^\n]{0,80}all",
        ],
    },
    "persisted": {
        "text": "勾选持久化：写进本地存储，启动时还能读回",
        "all": [
            r"(?:getStringSet|getString|getBoolean)\(",
            r"(?:putStringSet|putString|putBoolean)\(",
            r"(?i)\"[^\"]*(?:mood|emotion|allow|white)[^\"]*\"",
        ],
    },
    "fallback": {
        "text": "未勾选回退平常：不在白名单时落到 NEUTRAL",
        "any": [
            r"\bin\b[^\n]{0,120}else[^\n]{0,80}NEUTRAL",
            r"(?:contains\(|not\s+in\b|!in\b)[^\n]{0,160}NEUTRAL",
            r"resolve\([^\n]{0,200}NEUTRAL",
        ],
    },
    "toggle_ui": {
        "text": "设置页逐项开关：每个表情一个控件，勾选即生效",
        "all": [
            r"(?:Checkbox|Switch)\s*\(",
            r"onCheckedChange",
            r"Emotion\.(?:entries|values\(\))",
        ],
    },
}


def _changed_files(ctx: Ctx) -> dict[str, str]:
    texts = {}
    for rel in ctx.evidence.get("patch_files", []):
        path = ctx.ws / rel
        if path.is_file():
            texts[rel] = path.read_text(encoding="utf-8", errors="replace")
    return texts


def run_probes(ctx: Ctx, names: list[str] | None = None) -> dict:
    wanted = names or list(PROBES)
    blob = "\n".join(_changed_files(ctx).values())
    results = {}
    for name in wanted:
        probe = PROBES.get(name)
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

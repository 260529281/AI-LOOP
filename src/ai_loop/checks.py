"""L1 机械校验：由代码判定，审批官没有权限让它通过。"""

from __future__ import annotations

import fnmatch

from . import sandbox
from .context import Ctx


def _artifact_nonempty(ctx: Ctx) -> dict:
    missing = [
        name
        for name in ctx.stage.get("produces", [])
        if not (ctx.run.artifact(ctx.stage["id"], name)).exists()
        or ctx.run.artifact(ctx.stage["id"], name).stat().st_size == 0
    ]
    detail = "产物齐全" if not missing else f"缺失或为空：{', '.join(missing)}"
    return {"check": "artifact_nonempty", "ok": not missing, "detail": detail}


def _patch_scope(ctx: Ctx) -> dict:
    patch = ctx.run.artifact(ctx.stage["id"], "change.patch")
    if not patch.exists():
        return {"check": "patch_scope", "ok": False, "detail": "没有 change.patch"}
    allowed = ctx.stage.get("patch_scope") or []
    files = sandbox.patch_files(patch)
    outside = [
        f for f in files if not any(fnmatch.fnmatch(f, pattern) for pattern in allowed)
    ]
    ctx.evidence["patch_files"] = files
    ok = bool(files) and not outside
    ctx.evidence["scope_ok"] = ok
    detail = f"改动文件：{', '.join(files) or '无'}"
    if outside:
        detail += f"；越界：{', '.join(outside)}"
    return {"check": "patch_scope", "ok": ok, "detail": detail}


def _patch_applies(ctx: Ctx) -> dict:
    patch = ctx.run.artifact(ctx.stage["id"], "change.patch")
    if not patch.exists():
        return {"check": "patch_applies", "ok": False, "detail": "没有 change.patch"}
    ok, log = sandbox.git_apply(ctx.ws, patch, check_only=True)
    if not ok:
        ctx.evidence["patch_applied"] = False
        return {"check": "patch_applies", "ok": False, "detail": f"试应用失败：{log[:400]}"}
    ok, log = sandbox.git_apply(ctx.ws, patch)
    ctx.evidence["patch_applied"] = ok
    detail = "已应用到副本" if ok else f"应用失败：{log[:400]}"
    return {"check": "patch_applies", "ok": ok, "detail": detail}


def _build(ctx: Ctx) -> dict:
    if not ctx.evidence.get("patch_applied"):
        ctx.evidence["build_ok"] = False
        return {"check": "build", "ok": False, "detail": "补丁没应用成功，跳过构建"}
    ok, code, log = sandbox.gradle_build(ctx.ws, ctx.cfg.project["build"])
    ctx.build_log = log
    # 每轮单独留证，别让返工把上一轮的失败日志覆盖掉。
    ctx.run.write_artifact(ctx.stage["id"], f"build-{ctx.attempt}.log", log)
    ctx.evidence["build_ok"] = ok
    ctx.evidence["build_returncode"] = code
    ctx.evidence["build_tail"] = sandbox.error_excerpt(log)
    detail = "assembleDebug 通过" if ok else f"assembleDebug 失败（exit={code}）：\n{ctx.evidence['build_tail']}"
    return {"check": "build", "ok": ok, "detail": detail}


CHECKERS = {
    "artifact_nonempty": _artifact_nonempty,
    "patch_scope": _patch_scope,
    "patch_applies": _patch_applies,
    "build": _build,
}


def run_l1(ctx: Ctx) -> dict:
    results = []
    for name in ctx.stage.get("checks", []):
        results.append(CHECKERS[name](ctx))
    ok = all(item["ok"] for item in results)
    ctx.evidence["l1_ok"] = ok
    ctx.run.log(
        "l1",
        ctx.stage["id"],
        attempt=ctx.attempt,
        ok=ok,
        results=[{k: r[k] for k in ("check", "ok", "detail")} for r in results],
    )
    return {"ok": ok, "checks": results}

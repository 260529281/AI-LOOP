from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

PATCH_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def prepare(project_root: Path, workspace: Path, exclude_dirs: list[str]) -> Path:
    """每轮实现都在全新副本上做，坏补丁不会污染下一轮。"""
    if workspace.exists():
        shutil.rmtree(workspace)
    skip = set(exclude_dirs) | {".git"}
    shutil.copytree(
        project_root,
        workspace,
        ignore=lambda _dir, names: {n for n in names if n in skip},
    )
    return workspace


def patch_files(patch: Path) -> list[str]:
    text = patch.read_text(encoding="utf-8", errors="replace")
    return [m.strip() for m in PATCH_FILE_RE.findall(text)]


def patch_stats(patch: Path) -> dict:
    text = patch.read_text(encoding="utf-8", errors="replace")
    added = sum(1 for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return {"added": added, "removed": removed, "files": patch_files(patch)}


def git_apply(workspace: Path, patch: Path, check_only: bool = False) -> tuple[bool, str]:
    args = ["git", "apply", "-p1", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    args.append(str(patch.resolve()))
    proc = subprocess.run(
        args, cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def gradle_build(workspace: Path, build_cfg: dict) -> tuple[bool, int, str]:
    exe = Path(build_cfg["gradle_bin"]) / build_cfg["command"][0]
    head = ["cmd", "/c", str(exe)] if str(exe).lower().endswith(".bat") else [str(exe)]
    args = head + build_cfg["command"][1:]
    env = os.environ.copy()
    env["JAVA_HOME"] = build_cfg["java_home"]
    env["PATH"] = build_cfg["gradle_bin"] + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            args,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=build_cfg["timeout_sec"],
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return False, 124, f"构建超时（{build_cfg['timeout_sec']}s）\n{output}"
    return proc.returncode == 0, proc.returncode, (proc.stdout + proc.stderr).strip()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def error_excerpt(log: str, max_errors: int = 5, tail: int = 6) -> str:
    """构建日志里真正有用的部分：编译错误行 + 结尾几行，而不是 Gradle 的 Help 尾巴。"""
    lines = log.splitlines()
    errors = [
        line.strip()
        for line in lines
        if line.lstrip().startswith("e: ") or "error:" in line.lower() or "FAILURE:" in line
    ][:max_errors]
    picked = errors or []
    for line in lines[-tail:]:
        if line.strip() and line.strip() not in picked:
            picked.append(line.rstrip())
    return "\n".join(picked)

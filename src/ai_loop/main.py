from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from . import sandbox
from .config import ROOT, Config, load_yaml
from .pipeline import Escalated, run_pipeline, verify_patch
from .state import Run


def _latest_run() -> Run:
    runs = sorted(p for p in (ROOT / "runs").glob("*") if (p / "state.json").exists())
    if not runs:
        raise SystemExit("还没有任何运行记录")
    return Run(ROOT, runs[-1].name)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        run = run_pipeline(args.task, drill=args.drill, run_id=args.run_id)
    except Escalated as exc:
        print(f"\n[BLOCKED] {exc.stage} 超出返工预算，流水线停在原地，等待人工决策。")
        for block in exc.blocking:
            print(f"  - {block.get('criterion')}: {block.get('reason')}｜{block.get('evidence')[:200]}")
        return 2
    except Exception as exc:
        # 模型侧错误（余额、网络、鉴权）也要把状态落盘，别让运行记录停在 running
        try:
            run = Run(ROOT, args.run_id) if args.run_id else _latest_run()
        except SystemExit:
            run = None
        if run is not None and run.state.get("status") == "running":
            run.state["status"] = "error"
            run.state["error"] = str(exc)[:500]
            run.save()
            run.log("error", reason=str(exc)[:500])
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        return 3
    print(f"\n[PASSED] run_id={run.run_id}")
    print(f"  报告：{run.dir / 'report.md'}")
    print(f"  事件：{run.events_path}")
    patch = run.artifact("s2-implement", "change.patch")
    if patch.exists():
        print(f"  补丁：{patch}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run = Run(ROOT, args.run_id) if args.run_id else _latest_run()
    state = run.state
    print(f"run_id={state['run_id']}  status={state['status']}  stage={state.get('stage')}")
    print(f"attempts={json.dumps(state.get('attempts', {}), ensure_ascii=False)}")
    print(f"verdicts={json.dumps(state.get('verdicts', {}), ensure_ascii=False)}")
    report = run.dir / "report.md"
    if report.exists():
        print(f"report={report}")
    return 0


def _backup_targets(root: Path, patch: Path, backup: Path) -> int:
    """项目不是 git 仓库，落地前把补丁会覆盖的原文件拷一份，留作回滚。"""
    saved, seen = 0, set()
    for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("+++ b/"):
            continue
        rel = line[6:].strip()
        if rel in seen:
            continue
        seen.add(rel)
        src = root / rel
        if not src.is_file():
            continue
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        saved += 1
    return saved


def cmd_verify(args: argparse.Namespace) -> int:
    patch_text = Path(args.patch).read_text(encoding="utf-8", errors="replace")
    run = verify_patch(args.task, patch_text, run_id=args.run_id)
    print(f"[verify] {run.run_id} status={run.state['status']}（未调用模型）")
    return 0 if run.state["status"] == "passed" else 1


def cmd_apply(args: argparse.Namespace) -> int:
    run = Run(ROOT, args.run_id) if args.run_id else _latest_run()
    if run.state["status"] != "passed":
        raise SystemExit(f"这次运行状态是 {run.state['status']}，不能落地")
    cfg = Config()
    patch = run.artifact("s2-implement", "change.patch")
    if not patch.exists():
        raise SystemExit("没有 change.patch")
    root = Path(cfg.project["root"])
    ok, log = sandbox.git_apply(root, patch, check_only=True)
    print(f"试应用：{'通过' if ok else '失败'}")
    if log:
        print(log[:800])
    if not ok:
        return 1
    if args.check:
        print("只做检查，没有改动项目")
        return 0
    backup = run.dir / "backup"
    saved = _backup_targets(root, patch, backup)
    print(f"已备份 {saved} 个原文件到 {backup}")
    ok, log = sandbox.git_apply(root, patch)
    print(f"已应用到 {root}" if ok else f"应用失败：{log[:800]}")
    print("落地只改源码：没有构建，也没有安装到手机。")
    run.log("apply", run_id=run.run_id, ok=ok)
    return 0 if ok else 1


def cmd_compare(args: argparse.Namespace) -> int:
    import difflib

    left, right = Run(ROOT, args.left), Run(ROOT, args.right)
    print(f"A = {left.run_id}  status={left.state['status']}")
    print(f"B = {right.run_id}  status={right.state['status']}")
    print(f"{'环节':<14}{'A 尝试/裁决':<18}{'B 尝试/裁决':<18}")
    for stage in Config().stages:
        sid = stage["id"]
        row = []
        for run in (left, right):
            report = (run.dir / sid)
            verdicts = sorted(report.glob("verdict-*.json"))
            import json

            stage_verdicts = [
                json.loads(p.read_text(encoding="utf-8")) for p in verdicts
            ]
            stage_verdicts = [v for v in stage_verdicts if v.get("label", "stage") == "stage"]
            last = stage_verdicts[-1]["verdict"] if stage_verdicts else "-"
            row.append(f"{run.state['attempts'].get(sid, '-')} / {last}")
        print(f"{stage['title']:<12}{row[0]:<20}{row[1]:<20}")

    pa = left.artifact("s2-implement", "change.patch")
    pb = right.artifact("s2-implement", "change.patch")
    if pa.exists() and pb.exists():
        diff = list(
            difflib.unified_diff(
                pa.read_text(encoding="utf-8").splitlines(),
                pb.read_text(encoding="utf-8").splitlines(),
                fromfile=f"A:{left.run_id}",
                tofile=f"B:{right.run_id}",
                lineterm="",
            )
        )
        print(f"\n补丁差异：{len(diff)} 行")
        for line in diff[:80]:
            print(line)
        if len(diff) > 80:
            print(f"...（共 {len(diff)} 行，省略剩余）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-loop", description="带硬门禁的 AI 闭环协作 MVP")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑一条流水线")
    p_run.add_argument("task", help="任务卡 yaml 路径")
    p_run.add_argument("--drill", action="store_true", help="第一轮注入残缺实现，验证门禁会拦")
    p_run.add_argument("--run-id", default=None)
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="查看运行状态")
    p_status.add_argument("--run-id", default=None)
    p_status.set_defaults(func=cmd_status)

    p_verify = sub.add_parser("verify", help="只校验外部补丁，不调用模型")
    p_verify.add_argument("--task", required=True, help="任务卡 yaml 路径")
    p_verify.add_argument("--patch", required=True, help="外部补丁文件")
    p_verify.add_argument("--run-id", default=None)
    p_verify.set_defaults(func=cmd_verify)

    p_apply = sub.add_parser("apply", help="把通过验收的补丁落到真实项目")
    p_apply.add_argument("--run-id", default=None)
    p_apply.add_argument("--check", action="store_true", help="只试应用，不改项目")
    p_apply.set_defaults(func=cmd_apply)

    p_cmp = sub.add_parser("compare", help="对比两次运行（例如桩 vs 真模型）")
    p_cmp.add_argument("left")
    p_cmp.add_argument("right")
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""ai-loop 的独立入口：本地网页控制台。

- 只绑 127.0.0.1（默认 7878），不与其他 agent 共享会话或端口
- 只读写本项目自己的 runs/ 与 tasks/，不 import 任何 Qoder / DSH 组件
- 运行通过子进程 `python -m ai_loop run` 发起，界面只读 events.jsonl，
  所以关掉网页不会影响流水线，流水线崩了也不会拖垮界面
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..config import ROOT, Config
from ..state import Run

STATIC = Path(__file__).resolve().parent / "static"
RUNS = ROOT / "runs"
TASKS = ROOT / "tasks"
MAX_BODY = 1 << 20
MAX_FILE = 512 * 1024
RUN_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,40}$")
STAGE_RE = re.compile(r"^s\d+-[a-z-]+$")

_procs: dict[str, dict] = {}
_lock = threading.Lock()


def _now_id() -> str:
    base = time.strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 1
    while (RUNS / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def _safe(base: Path, rel: str) -> Path:
    path = (base / rel).resolve()
    path.relative_to(base.resolve())
    return path


def _run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id or ""):
        raise ValueError("非法的 run_id")
    path = RUNS / run_id
    if not path.is_dir():
        raise ValueError("找不到这次运行")
    return path


def _state(run_id: str) -> dict:
    path = RUNS / run_id / "state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _proc_state(run_id: str) -> dict:
    info = _procs.get(run_id)
    if not info:
        return {"spawned": False, "alive": False}
    code = info["proc"].poll()
    return {"spawned": True, "alive": code is None, "returncode": code}


STALE_AFTER = 300  # 秒。模型单次调用可能很久，超过这个还没新事件才判定为中断


def _is_stale(run_id: str) -> bool:
    if _state(run_id).get("status") != "running":
        return False
    info = _procs.get(run_id)
    if info and info["proc"].poll() is None:
        return False
    stamps = []
    for name in ("state.json", "events.jsonl"):
        path = RUNS / run_id / name
        if path.exists():
            stamps.append(path.stat().st_mtime)
    return (time.time() - max(stamps)) > STALE_AFTER if stamps else True


def _active_run() -> str | None:
    for rid, info in _procs.items():
        if info["proc"].poll() is None:
            return rid
    return None


def _busy_run() -> str | None:
    """界面上看到的占用：本界面发起的，或命令行发起且还没结束的。"""
    busy = _active_run()
    if busy:
        return busy
    for path in RUNS.glob("*"):
        if not (path / "state.json").exists():
            continue
        if _state(path.name).get("status") == "running" and not _is_stale(path.name):
            return path.name
    return None


def _tasks() -> list[dict]:
    cards = []
    if TASKS.is_dir():
        for path in sorted(TASKS.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cards.append(
                {
                    "task_id": path.stem,
                    "file": path.name,
                    "id": data.get("id", path.stem),
                    "title": data.get("title", path.stem),
                    "requirement": data.get("requirement", ""),
                    "risk_tier": data.get("risk_tier", "-"),
                }
            )
    return cards


def _task_of(run_id: str) -> dict:
    path = RUNS / run_id / "task.yaml"
    if path.exists():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def _artifacts(run_id: str) -> list[dict]:
    base = RUNS / run_id
    items = []
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in {"events.jsonl"}:
            items.append(
                {
                    "stage": "",
                    "name": path.name,
                    "rel": path.name,
                    "bytes": path.stat().st_size,
                    "mtime": int(path.stat().st_mtime),
                }
            )
    for stage_dir in sorted(base.iterdir()):
        if not stage_dir.is_dir() or not STAGE_RE.fullmatch(stage_dir.name):
            continue
        for path in sorted(stage_dir.iterdir()):
            if path.is_file():
                items.append(
                    {
                        "stage": stage_dir.name,
                        "name": path.name,
                        "rel": f"{stage_dir.name}/{path.name}",
                        "bytes": path.stat().st_size,
                        "mtime": int(path.stat().st_mtime),
                    }
                )
    return items


def _runs() -> list[dict]:
    rows = []
    for path in sorted(RUNS.glob("*"), reverse=True):
        if not path.is_dir() or not (path / "state.json").exists():
            continue
        state = _state(path.name)
        task = _task_of(path.name)
        rows.append(
            {
                "run_id": path.name,
                "status": state.get("status", "?"),
                "stage": state.get("stage"),
                "mode": state.get("mode", "run"),
                "attempts": state.get("attempts", {}),
                "verdicts": state.get("verdicts", {}),
                "created_at": state.get("created_at"),
                "title": task.get("title") or task.get("id") or "",
                "process": _proc_state(path.name),
                "stale": _is_stale(path.name),
            }
        )
    rows.sort(key=lambda r: (r.get("created_at") or "", r["run_id"]), reverse=True)
    return rows[:80]


def _events(run_id: str, offset: int) -> dict:
    path = RUNS / run_id / "events.jsonl"
    if not path.exists():
        return {"events": [], "next": 0}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    offset = max(0, min(offset, len(lines)))
    parsed = []
    for line in lines[offset:]:
        try:
            parsed.append(json.loads(line))
        except Exception:
            parsed.append({"event": "raw", "text": line[:400]})
    return {"events": parsed, "next": len(lines)}


def _health() -> dict:
    cfg = Config()
    return {
        "ok": True,
        "root": str(ROOT),
        "backend": cfg.backend,
        "model": os.getenv("AI_LOOP_MODEL_NAME", "deepseek/deepseek-v4-pro"),
        "base_url": os.getenv("AI_LOOP_BASE_URL", ""),
        "port": _PORT,
        "active_run": _busy_run(),
        "stages": [
            {
                "id": s["id"],
                "title": s["title"],
                "agent": s["agent"],
                "name": cfg.agents[s["agent"]]["name"],
                "readback": bool(s.get("readback")),
            }
            for s in cfg.stages
        ],
    }


def _spawn_run(body: dict) -> dict:
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("缺少 task_id")
    src = _safe(TASKS, f"{task_id}.yaml")
    if not src.is_file():
        raise ValueError("任务卡不存在")

    with _lock:
        busy = _busy_run()
        if busy:
            raise RuntimeError(f"上一次运行 {busy} 还没结束，流水线独占构建资源")

        run_id = _now_id()
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        card = yaml.safe_load(src.read_text(encoding="utf-8"))
        requirement = str(body.get("requirement") or "").strip()
        if requirement and requirement != str(card.get("requirement", "")).strip():
            card["requirement"] = requirement
        task_file = run_dir / "task.yaml"
        task_file.write_text(
            yaml.safe_dump(card, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        backend = str(body.get("backend") or "").strip().lower()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        if backend in {"stub", "cloud"}:
            env["AI_LOOP_BACKEND"] = backend

        cmd = [sys.executable, "-m", "ai_loop", "run", str(task_file), "--run-id", run_id]
        if body.get("drill"):
            cmd.append("--drill")

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log_path = run_dir / "ui-console.log"
        handle = log_path.open("w", encoding="utf-8", errors="replace", buffering=1)
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        _procs[run_id] = {"proc": proc, "log": handle, "started": time.time()}

    def _watch() -> None:
        proc.wait()
        try:
            handle.close()
        except Exception:
            pass

    threading.Thread(target=_watch, daemon=True).start()
    return {"run_id": run_id, "backend": env.get("AI_LOOP_BACKEND", "cloud")}


def _verify(body: dict) -> dict:
    """外部补丁校验：补丁来自界面粘贴（Qoder 产出），只跑校验，不调用模型。"""
    task_id = str(body.get("task_id") or "").strip()
    patch = str(body.get("patch") or "")
    if not task_id:
        raise ValueError("缺少任务卡")
    if not patch.strip():
        raise ValueError("补丁是空的")
    if "diff --git" not in patch and "+++ b/" not in patch:
        raise ValueError("这不像 git 补丁：缺少 diff --git / +++ 行")
    src = _safe(TASKS, f"{task_id}.yaml")
    if not src.is_file():
        raise ValueError("任务卡不存在")

    with _lock:
        busy = _busy_run()
        if busy:
            raise RuntimeError(f"上一次运行 {busy} 还没结束，流水线独占构建资源")

        run_id = _now_id()
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        patch_path = run_dir / "external.patch"
        patch_path.write_text(patch.strip() + "\n", encoding="utf-8")
        card = yaml.safe_load(src.read_text(encoding="utf-8"))
        task_file = run_dir / "task.yaml"
        task_file.write_text(
            yaml.safe_dump(card, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        cmd = [
            sys.executable, "-m", "ai_loop", "verify",
            "--task", str(task_file), "--patch", str(patch_path), "--run-id", run_id,
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        handle = (run_dir / "ui-console.log").open("w", encoding="utf-8", errors="replace", buffering=1)
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env,
            stdout=handle, stderr=subprocess.STDOUT, creationflags=flags,
        )
        _procs[run_id] = {"proc": proc, "log": handle, "started": time.time()}

    def _watch() -> None:
        proc.wait()
        try:
            handle.close()
        except Exception:
            pass

    threading.Thread(target=_watch, daemon=True).start()
    return {"run_id": run_id, "backend": "verify"}


def _apply(run_id: str, check: bool) -> dict:
    state = _state(run_id)
    if state.get("status") != "passed" and not check:
        raise RuntimeError(f"状态是 {state.get('status')}，没通过验收不能落地")
    cmd = [sys.executable, "-m", "ai_loop", "apply", "--run-id", run_id]
    if check:
        cmd.append("--check")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run(
        cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, creationflags=flags,
    )
    result = {"returncode": done.returncode, "output": (done.stdout + done.stderr)[-8000:]}
    Run(ROOT, run_id).log(
        "apply",
        None,
        check=check,
        ok=done.returncode == 0,
        output=result["output"][-1500:],
    )
    return result


def _stop(run_id: str) -> dict:
    info = _procs.get(run_id)
    if not info:
        raise RuntimeError("这次运行不是从本界面发起的")
    proc = info["proc"]
    if proc.poll() is not None:
        return {"stopped": False, "reason": "已经结束"}
    pid = str(proc.pid)
    subprocess.run(
        ["taskkill", "/PID", pid, "/T", "/F"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"stopped": True, "pid": pid}


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-loop-ui/1.0"

    def log_message(self, fmt: str, *args) -> None:
        pass

    # ---------- helpers ----------
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str, ctype: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _static(self, rel: str) -> None:
        target = _safe(STATIC, rel)
        if not target.is_file():
            self._text(404, "not found", "text/plain; charset=utf-8")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        self._text(200, target.read_text(encoding="utf-8"), ctype)

    # ---------- routes ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/health":
                return self._json(200, _health())
            if path == "/api/tasks":
                return self._json(200, {"tasks": _tasks()})
            if path == "/api/runs":
                return self._json(200, {"runs": _runs()})
            m = re.fullmatch(r"/api/runs/([^/]+)", path)
            if m:
                run_id = m.group(1)
                _run_dir(run_id)
                state = _state(run_id)
                report = RUNS / run_id / "report.md"
                return self._json(
                    200,
                    {
                        "run_id": run_id,
                        "state": state,
                        "task": _task_of(run_id),
                        "process": _proc_state(run_id),
                        "stale": _is_stale(run_id),
                        "artifacts": _artifacts(run_id),
                        "report": report.read_text(encoding="utf-8") if report.exists() else "",
                    },
                )
            m = re.fullmatch(r"/api/runs/([^/]+)/events", path)
            if m:
                _run_dir(m.group(1))
                offset = int((query.get("from") or ["0"])[0])
                return self._json(200, _events(m.group(1), offset))
            m = re.fullmatch(r"/api/runs/([^/]+)/file", path)
            if m:
                run_id = m.group(1)
                _run_dir(run_id)
                rel = (query.get("path") or [""])[0]
                target = _safe(RUNS / run_id, rel)
                if not target.is_file() or target.stat().st_size > MAX_FILE:
                    raise ValueError("文件不存在或过大")
                return self._text(
                    200, target.read_text(encoding="utf-8", errors="replace"),
                    "text/plain; charset=utf-8",
                )
            return self._json(404, {"error": "没有这个接口"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/runs":
                return self._json(200, _spawn_run(body))
            if path == "/api/verify":
                return self._json(200, _verify(body))
            m = re.fullmatch(r"/api/runs/([^/]+)/apply", path)
            if m:
                return self._json(200, _apply(m.group(1), bool(body.get("check"))))
            m = re.fullmatch(r"/api/runs/([^/]+)/stop", path)
            if m:
                return self._json(200, _stop(m.group(1)))
            return self._json(404, {"error": "没有这个接口"})
        except (ValueError, RuntimeError) as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


_PORT = 7878


def _already_running(host: str, port: int) -> bool:
    """端口被占时判断是不是本项目的界面，而不是别的程序。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("ok")) and "active_run" in data and "stages" in data
    except Exception:
        return False


def _port_state(host: str, port: int) -> str:
    """必须先探测再绑定：Windows 的 SO_REUSEADDR 允许两个进程绑同一端口，
    第二个会静默变成收不到请求的僵尸进程。"""
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.4)
        if sock.connect_ex((host, port)) != 0:
            return "free"
    return "our" if _already_running(host, port) else "other"


def main(argv: list[str] | None = None) -> int:
    global _PORT
    parser = argparse.ArgumentParser(prog="ai-loop-ui", description="ai-loop 独立网页入口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7878)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)
    _PORT = args.port

    url = f"http://{args.host}:{args.port}/"
    state = _port_state(args.host, args.port)
    if state == "our":
        print(f"[ai-loop-ui] {url} 已经有本项目的服务在跑，直接用它", flush=True)
        if args.open:
            webbrowser.open(url)
        return 0
    if state == "other":
        print(f"[ai-loop-ui] 端口 {args.port} 被别的程序占用", flush=True)
        print("[ai-loop-ui] 换一个端口：启动入口.cmd --port 7879", flush=True)
        return 2

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[ai-loop-ui] 启动失败：端口 {args.port} 绑不上（{exc}）", flush=True)
        print("[ai-loop-ui] 换一个端口：启动入口.cmd --port 7879", flush=True)
        return 2

    server.daemon_threads = True
    print(f"[ai-loop-ui] {url}  (独立入口：只读写 {ROOT}\\runs)", flush=True)
    print("[ai-loop-ui] Ctrl+C 退出；本界面不与 Qoder / DSH 共享会话", flush=True)
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ai-loop-ui] 已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

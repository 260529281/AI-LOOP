"""一个假的 OpenAI 兼容端点，用来在没有 API key 的情况下验证 cloud 通道的接线。

它按 prompt 里的阶段标记返回 fixture 产物，起到「真模型位置」的替身作用：
- 走的是真实的 litellm/HTTP 路径（不是桩 LLM 类）
- 会真的吃进 40KB+ 的源码上下文
- 产物仍要过 L1 构建与 L2 审批，不会被放水

用法：
    python tools/fake_openai.py --port 8765
    AI_LOOP_BACKEND=cloud AI_LOOP_MODEL_NAME=openai/fake \
    AI_LOOP_BASE_URL=http://127.0.0.1:8765/v1 AI_LOOP_API_KEY=dummy \
    PYTHONPATH=src ./.venv/Scripts/python.exe -m ai_loop run tasks/mood-whitelist.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "mood-whitelist"

READBACK = """## 上游结论
- spec 要求四套心情默认全开、可逐项勾选、未勾选回退「平常」、勾选持久化，并只改 app/src/main/java。

## 依赖契约
- 我依赖 AvatarViewModel 暴露 allowedEmotions 与 toggleEmotionAllowed，MainActivity 只负责绑勾选框。
- 改动范围限定在 app/src/main/java/ 内，构建必须是 assembleDebug。

## 疑点
- 无
"""

REVIEW = """# 审查结论

证据：`gradle --offline assembleDebug` 退出码 0（见 `s2-implement/build-*.log`）。
证据：补丁只改两个 Kotlin 文件，均在 app/src/main/java/ 下。

## 阻塞项
- 无
"""

ACCEPTANCE = """# 验收结论

依据审查结论与构建证据逐条判定：

| 标准 | 结论 | 证据 |
|---|---|---|
| C1 只改 app/src/main/java | PASS | 补丁文件列表 |
| C2 补丁可干净应用 | PASS | git apply --check |
| C3 白名单状态与开关 | PASS | AvatarViewModel.kt |
| C4 setEmotion 收敛 | PASS | 正则命中 |
| C5 设置页勾选框 | PASS | MainActivity.kt |
| C6 构建通过 | PASS | assembleDebug 退出码 0 |
"""


def choose(prompt: str) -> tuple[str, str]:
    # 「## 输出要求」之后是本阶段契约，用它判阶段；上游产物里会出现别的阶段的标题。
    marker = prompt.rfind("## 输出要求")
    tail = prompt[marker:] if marker >= 0 else prompt[-800:]
    if "拆解文档" in tail:
        return "decompose", (FIX / "spec.md").read_text(encoding="utf-8")
    if "unified diff" in tail:
        return "implement", (FIX / "implement.patch").read_text(encoding="utf-8")
    if "审查结论" in tail:
        return "review", REVIEW
    if "验收结论" in tail:
        return "accept", ACCEPTANCE
    if "复述" in prompt and "疑点" in prompt:
        return "readback", READBACK
    return "unknown", "无法识别阶段"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages", [])
        text = "\n".join(
            m.get("content", "") if isinstance(m, dict) else str(m) for m in messages
        )
        phase, content = choose(text)
        print(f"[fake] {self.path} phase={phase} prompt_chars={len(text)}", file=sys.stderr)
        body = json.dumps(
            {
                "id": "fake-1",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "fake"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": len(content) // 4, "total_tokens": 0},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[fake] listening on http://127.0.0.1:{args.port}/v1", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()

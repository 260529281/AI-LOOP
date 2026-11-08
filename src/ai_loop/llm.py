from __future__ import annotations

import os

from crewai import LLM


def build_real_llm() -> LLM:
    """换成真实模型只需要设环境变量，流水线一行都不用改。"""
    model = os.getenv("AI_LOOP_MODEL_NAME", "deepseek/deepseek-chat")
    kwargs = {"api_key": os.getenv("AI_LOOP_API_KEY", "")}
    base_url = os.getenv("AI_LOOP_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return LLM(model=model, **kwargs)

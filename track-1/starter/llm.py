"""A Featherless client that counts tokens per model.

    llm = LLM()                                   # key and endpoint from the environment
    text = llm.ask("zai-org/GLM-4.7-Flash", "How many failures does this ask for? ...")
    llm.usage
    # {"zai-org/GLM-4.7-Flash": {"prompt_tokens": 812, "completion_tokens": 40, "calls": 1}}

Return `llm.usage` as your Solution's `usage` and run.py writes it to usage.jsonl,
per case and per model. `cost.py` turns that into dollars.

The key comes from FEATHERLESS_API_KEY and the endpoint from FEATHERLESS_BASE_URL
(default https://api.featherless.ai/v1). That is how we run your agent on our key,
so never hard-code either.
"""
from __future__ import annotations

import os
import re

from openai import OpenAI

DEFAULT_BASE_URL = "https://api.featherless.ai/v1"


class LLM:
    def __init__(self) -> None:
        key = os.environ.get("FEATHERLESS_API_KEY")
        if not key:
            raise RuntimeError("FEATHERLESS_API_KEY is not set")
        self.client = OpenAI(api_key=key,
                             base_url=os.environ.get("FEATHERLESS_BASE_URL", DEFAULT_BASE_URL))
        self.usage: dict[str, dict[str, int]] = {}

    def ask(self, model: str, prompt: str | list[dict], **kwargs) -> str:
        """One chat call. `prompt` is a string (one user message) or a message list."""
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        r = self.client.chat.completions.create(model=model, messages=messages, **kwargs)
        u = self.usage.setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0,
                                          "calls": 0})
        u["calls"] += 1
        if r.usage:
            u["prompt_tokens"] += r.usage.prompt_tokens or 0
            u["completion_tokens"] += r.usage.completion_tokens or 0
        text = r.choices[0].message.content or ""
        # GLM models can think out loud first; keep only the answer
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

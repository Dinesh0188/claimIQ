"""Environment-backed settings.

One provider abstraction: Groq, DeepSeek and OpenAI all speak the OpenAI wire
format, so `LLM_BASE_URL` + `LLM_MODEL` is the entire difference between them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    vision_model: str
    ai_enabled: bool

    @property
    def has_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.endswith("_here")

    @property
    def ai_on(self) -> bool:
        """AI is usable only if it is switched on AND a real key is present."""
        return self.ai_enabled and self.has_key


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
        vision_model=os.getenv("VISION_MODEL", "qwen/qwen3.6-27b"),
        ai_enabled=os.getenv("AI_ENABLED", "true").lower() == "true",
    )

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


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on"} if raw else default


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    vision_model: str
    ai_enabled: bool

    # --- deployment posture -------------------------------------------------
    #
    # Every one of these defaults to the single-user local demo, so cloning the repo
    # and running `.\start.ps1` behaves exactly as it did before this section existed.
    # An operator turns each on deliberately; nothing is half-enabled by accident.

    # Principal definitions, `key:tenant:scopes` separated by commas. Empty means
    # authentication is OFF -- stated in /health so an unauthenticated deployment is
    # never a silent one.
    api_keys_raw: str = ""
    # Allowed browser origins for the SPA. Empty means same-origin only, i.e. no CORS
    # headers at all, which is the correct default for a bundled frontend.
    cors_origins_raw: str = ""
    # A single regex matched against the request Origin header, in addition to the
    # exact list above. Exists for Vercel: every preview deployment gets its own
    # subdomain (e.g. claimiq-git-fix-123.vercel.app), so a fixed list would mean
    # editing an env var on every PR. The production domain still belongs in the
    # exact list -- this is deliberately the looser, secondary check, not a
    # replacement for it.
    cors_origin_regex: str = ""
    # Hard ceiling on an upload, enforced before the bytes are buffered. 25 MB fits a
    # 40-page scanned bill at 300 dpi with room to spare.
    max_upload_bytes: int = 25 * 1024 * 1024
    # Requests per minute per principal, sliding window. 0 disables.
    rate_limit_per_minute: int = 120
    # Claims a single batch may carry, and how many run at once.
    max_batch_size: int = 500
    batch_workers: int = 4
    # Append-only ledger of every audit. On by default: it is the compliance artefact,
    # and a claims auditor that cannot say what it decided last Tuesday is not one.
    ledger_enabled: bool = True
    # Emit one JSON object per request on stdout instead of uvicorn's text line.
    json_logs: bool = False
    # How long clinical detail is kept. Two years covers an Indian insurer's audit
    # cycle; traces are debugging aids and age out far sooner. Nothing purges
    # automatically -- there is no scheduler in this process, so the operator owns the
    # decision and triggers `POST /v1/retention/purge` (`dry_run=false`) from cron.
    claim_retention_days: int = 365 * 2
    trace_retention_days: int = 90

    # Bind host. Loopback by default so the local demo is never reachable off the
    # machine; Render sets CLAIMIQ_BIND=0.0.0.0 explicitly.
    bind_host: str = "127.0.0.1"
    # A public bind with authentication off is an insecure deployment. It is reported
    # via insecure_deployment() on /health; it is not refused here -- operators run
    # demo deployments deliberately.
    allow_insecure_demo: bool = False

    @property
    def has_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.endswith("_here")

    @property
    def ai_on(self) -> bool:
        """AI is usable only if it is switched on AND a real key is present."""
        return self.ai_enabled and self.has_key

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
        vision_model=os.getenv("VISION_MODEL", "qwen/qwen3.6-27b"),
        ai_enabled=os.getenv("AI_ENABLED", "true").lower() == "true",
        api_keys_raw=os.getenv("CLAIMIQ_API_KEYS", ""),
        cors_origins_raw=os.getenv("CLAIMIQ_CORS_ORIGINS", ""),
        cors_origin_regex=os.getenv("CLAIMIQ_CORS_ORIGIN_REGEX", ""),
        max_upload_bytes=_int("CLAIMIQ_MAX_UPLOAD_BYTES", 25 * 1024 * 1024),
        rate_limit_per_minute=_int("CLAIMIQ_RATE_LIMIT_PER_MINUTE", 120),
        max_batch_size=_int("CLAIMIQ_MAX_BATCH_SIZE", 500),
        batch_workers=_int("CLAIMIQ_BATCH_WORKERS", 4),
        ledger_enabled=_bool("CLAIMIQ_LEDGER", True),
        json_logs=_bool("CLAIMIQ_JSON_LOGS", False),
        claim_retention_days=_int("CLAIMIQ_CLAIM_RETENTION_DAYS", 365 * 2),
        trace_retention_days=_int("CLAIMIQ_TRACE_RETENTION_DAYS", 90),
        bind_host=os.getenv("CLAIMIQ_BIND", "127.0.0.1"),
        allow_insecure_demo=_bool("CLAIMIQ_ALLOW_INSECURE_DEMO", False),
    )


def insecure_deployment() -> bool:
    """True when the server would bind publicly with auth off — a dangerous posture."""
    s = settings()
    loopback = s.bind_host in ("127.0.0.1", "localhost", "::1")
    return not loopback and not s.api_keys_raw and not s.allow_insecure_demo

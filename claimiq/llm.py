"""Provider-agnostic LLM client with schema-validated output.

Three things this does that a bare `client.chat.completions.create` does not:

1. Verifies the configured model actually exists before the demo depends on it.
2. Coerces output into a Pydantic model, re-prompting with the validation error
   when the model gets the shape wrong (bounded -- it gives up rather than looping).
3. Caches on a content hash, so a repeated demo is instant and free.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, TypeVar

from diskcache import Cache
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from claimiq.config import ROOT, settings
from claimiq.providers import Provider, active_provider
from claimiq.retrieval.corpus import corpus_version

T = TypeVar("T", bound=BaseModel)

_cache = Cache(str(ROOT / ".cache" / "llm"))

# Responses are scoped to the corpus content they were computed against; entries
# older than a day are stale enough that recomputing is cheaper than trusting them.
CACHE_TTL = 60 * 60 * 24  # seconds

# A long bill table is a long JSON document. Leaving this to the provider default
# is what produced "max completion tokens reached before generating a valid
# document" on the 37-row bill.
# Right-sized per call site, NOT one generous global.
#
# Providers count reserved completion tokens against the per-minute budget, so
# max_tokens is not free headroom -- it is spend. An 8000 default (added for the long
# extraction output) meant classification requested 3,353 prompt + 8,000 reserved =
# 11,353 against an 8,000/min cap, so every call 413'd and silently fell back to the
# deterministic path. The LLM was effectively switched off and nothing said so.
#
# Rule of thumb: prompt + max_tokens must fit the tier's TPM ceiling.
MAX_COMPLETION_TOKENS = 2000   # default: verdict batches, SQL, judgements
EXTRACTION_MAX_TOKENS = 5000   # a long bill table is a long JSON document
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF = 4.0  # seconds, multiplied by attempt number

# Free tiers cap a *single request* at prompt + reserved completion. Exceed it and the
# request 413s every time, however much daily quota is left -- waiting cannot help.
#
# A fixed EXTRACTION_MAX_TOKENS is therefore wrong: a small bill fits, a slightly
# larger one does not. 5000 reserved plus a ~3,100-token prompt requested 8,133
# against Groq's 8,000/min ceiling and failed by 133 tokens.
#
# So the budget is computed from the prompt actually being sent, and the caller
# splits its input when even the floor will not fit.
TPM_CEILING = 8000        # free-tier per-minute ceiling; raise for a paid tier
TPM_SAFETY_MARGIN = 900   # the provider's tokeniser will not match our estimate exactly
MIN_COMPLETION_TOKENS = 900  # below this a bill table cannot finish; split instead

# Deliberately pessimistic. The usual "~4 characters per token" rule is for prose;
# a bill is dense with digits, currency and abbreviations that tokenise far worse.
# Budgeting at 4 estimated ~7,400 tokens for a request Groq counted as 8,126 -- over
# the ceiling by 126, so it 413'd. Underestimating costs a failed request; over-
# estimating costs a slightly shorter reply, so the error is cheap in one direction
# and not the other.
CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fit_completion_budget(prompt_chars: int, wanted: int, ceiling: int = TPM_CEILING) -> int:
    """Largest completion budget that keeps this request under the provider's ceiling."""
    available = ceiling - TPM_SAFETY_MARGIN - int(prompt_chars / CHARS_PER_TOKEN) - 1
    return max(0, min(wanted, available))


class LLMUnavailable(RuntimeError):
    """No usable key/model. Callers fall back to deterministic text."""


class LLMCall(BaseModel):
    """One instrumented call, surfaced in the Trace screen."""

    node: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1


# The call ledger is per-context, not per-client.
#
# `try_client()` is lru_cached so every node shares one instance -- that is what makes
# token attribution work at all. But one shared instance also meant one shared `calls`
# list, and `trace.track` attributes usage by slicing it (`calls[before:]`). Under two
# concurrent audits in FastAPI's threadpool, claim A's slice picks up claim B's calls,
# so the Trace screen bills one claim for another's tokens. Same class of bug as the
# module-global run trace, same fix: isolate by context, not by instance.
_call_ledger: ContextVar[list[LLMCall] | None] = ContextVar("claimiq_llm_calls", default=None)


def call_ledger() -> list[LLMCall]:
    """This context's LLM calls, created on first use."""
    ledger = _call_ledger.get()
    if ledger is None:
        ledger = []
        _call_ledger.set(ledger)
    return ledger


def reset_call_ledger() -> None:
    """Start a fresh ledger. Called at the top of every audit run."""
    _call_ledger.set([])


class LLMClient:
    def __init__(self, provider: Provider | None = None) -> None:
        if not settings().ai_enabled:
            raise LLMUnavailable("AI_ENABLED is false. Running deterministic-only.")

        self.provider = provider or active_provider()
        if not self.provider.usable:
            raise LLMUnavailable(
                f"provider {self.provider.name!r} has no API key. "
                "Set one in providers.json, or LLM_API_KEY in .env."
            )

        self._client = OpenAI(
            base_url=self.provider.base_url, api_key=self.provider.api_key, timeout=90.0
        )
        self.model = self.provider.model
        self.vision_model = self.provider.vision_model

    @property
    def calls(self) -> list[LLMCall]:
        """Calls made in *this* context. See `call_ledger` for why it is not an attribute."""
        return call_ledger()

    @property
    def has_vision(self) -> bool:
        return self.provider.has_vision

    # --- model verification ------------------------------------------------

    def verify_models(self) -> list[str]:
        """Check configured model IDs against the provider's live catalog.

        Groq deprecated llama-3.3-70b-versatile and llama-3.1-8b-instant on
        2026-06-17. Hardcoding a model ID is how a demo dies on a 404, so we ask.
        Returns human-readable warnings; empty list means all good.
        """
        try:
            available = sorted(m.id for m in self._client.models.list().data)
        except Exception as exc:  # network/auth -- report, don't crash the app
            return [f"Could not reach {self.provider.base_url} to list models: {exc}"]

        warnings = []
        for label, model_id in (("model", self.model), ("vision_model", self.vision_model)):
            if not model_id or model_id in available:
                continue
            # Suggest near-misses. The submitted id "google/gemma-4-31b:free" was one
            # "-it" away from a real model, and a bare 404 would not have said so.
            stem = model_id.split("/")[-1].split(":")[0].rstrip("-it")
            close = [m for m in available if stem and stem in m][:6]
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            warnings.append(
                f"{self.provider.name}: {label}={model_id!r} is not available.{hint}"
            )
        return warnings

    # --- structured generation ---------------------------------------------

    def structured(
        self,
        *,
        node: str,
        system: str,
        user: str,
        schema: type[T],
        images: list[str] | None = None,
        temperature: float = 0.0,
        max_retries: int = 2,
        max_tokens: int = MAX_COMPLETION_TOKENS,
    ) -> T:
        """Generate JSON conforming to `schema`, re-prompting on validation failure.

        `max_tokens` is per call site on purpose -- a bill table needs a far longer
        completion than a verdict batch, and reserved tokens count against the
        per-minute budget rather than being free headroom.
        """
        if images and not self.vision_model:
            raise LLMUnavailable(
                f"provider {self.provider.name!r} has no vision model configured, so it "
                "cannot read scanned documents. Native PDFs still parse via the text layer."
            )

        model = self.vision_model if images else self.model
        key = _cache_key(model, system, user, schema.__name__, images)

        cached = _cache.get(key)
        if cached is not None:
            self.calls.append(LLMCall(node=node, model=model, cached=True))
            return schema.model_validate_json(cached)

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": f"{system}\n\nReply with JSON only, matching this schema:\n"
                f"{json.dumps(schema.model_json_schema())}",
            },
            {"role": "user", "content": _user_content(user, images)},
        ]

        # Trim the reserved completion to whatever this prompt leaves room for.
        # Without this a slightly longer document 413s where a shorter one succeeded,
        # and the failure looks like a quota problem rather than a sizing bug.
        ceiling = self.provider.tpm_ceiling
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        budget = fit_completion_budget(prompt_chars, max_tokens, ceiling)
        if budget < MIN_COMPLETION_TOKENS:
            raise LLMUnavailable(
                f"this request needs about {estimate_tokens(' ' * prompt_chars)} prompt "
                f"tokens, leaving only {budget} for the reply against {self.provider.name}'s "
                f"{ceiling}/min ceiling. Split the input, or switch to a provider with a "
                "higher limit."
            )
        max_tokens = budget

        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 2):
            raw, usage = self._complete(model, messages, temperature, max_tokens)
            try:
                parsed = schema.model_validate_json(_json_only(raw))
            except ValidationError as exc:
                last_error = exc
                # Targeted repair: show the model exactly what was wrong rather than
                # retrying the identical prompt and hoping.
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That did not validate:\n{exc}\nReturn corrected JSON only.",
                    },
                ]
                continue

            self.calls.append(
                LLMCall(
                    node=node,
                    model=model,
                    prompt_tokens=usage[0],
                    completion_tokens=usage[1],
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=attempt,
                )
            )
            _cache.set(key, parsed.model_dump_json(), expire=CACHE_TTL)
            return parsed

        raise LLMUnavailable(f"{schema.__name__} did not validate after {max_retries + 1} attempts: {last_error}")

    def _complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int = MAX_COMPLETION_TOKENS,
    ) -> tuple[str, tuple[int, int]]:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    # Without this the provider's default ceiling truncates long
                    # structured output mid-document and returns
                    # "max completion tokens reached before generating a valid
                    # document" -- which reads like the model failing when it is
                    # actually a budget we never set.
                    max_tokens=max_tokens,
                    # Not universally supported. Sending it to a provider that lacks
                    # it fails the whole request, so it is declared per provider and
                    # we lean on the prompt + validation retry when it is absent.
                    **(
                        {"response_format": {"type": "json_object"}}
                        if self.provider.supports_json_mode
                        else {}
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                if "rate_limit" not in str(exc).lower() or attempt == RATE_LIMIT_RETRIES:
                    raise
                time.sleep(RATE_LIMIT_BACKOFF * (attempt + 1))
                continue

            usage = response.usage
            return (
                response.choices[0].message.content or "",
                (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0),
            )

        raise LLMUnavailable("rate limited beyond retry budget")


def _json_only(raw: str) -> str:
    """Pull the JSON object out of a reply.

    Models without JSON mode wrap output in ```json fences or add a sentence of
    preamble. Rather than fail validation and burn a retry on something this
    mechanical, slice from the first brace to the last.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text.strip()


def _user_content(text: str, images: list[str] | None) -> Any:
    if not images:
        return text
    return [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": img}} for img in images
    ]


def _cache_key(model: str, system: str, user: str, schema: str, images: list[str] | None) -> str:
    # `corpus_version` is the module-global so a rule-corpus edit invalidates every
    # key computed under the old rules instead of serving a stale verdict.
    blob = "|".join([model, system, user, schema, corpus_version(), *(images or [])])
    return hashlib.sha256(blob.encode()).hexdigest()


@lru_cache(maxsize=1)
def try_client() -> LLMClient | None:
    """Return the shared client, or None if AI is off/unconfigured.

    Cached deliberately: the trace reads `client.calls` to attribute tokens and
    latency to nodes, which only works if every caller holds the same instance.
    """
    try:
        return LLMClient()
    except LLMUnavailable:
        return None

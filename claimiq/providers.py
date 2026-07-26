"""Named provider profiles, configured in `providers.json` rather than the environment.

Why a file and not env vars: switching provider means changing four related values at
once (base URL, key, chat model, vision model) plus a capability flag. Four env vars that
must be changed together is a footgun; one named profile is not. It also lets the UI offer
a live switch, which is genuinely useful when one provider's daily quota is exhausted.

`providers.json` holds real API keys and is gitignored. `providers.example.json` is the
committed template. If neither exists, settings fall back to `.env` as before.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from claimiq.config import ROOT, settings

CONFIG_PATH = ROOT / "providers.json"
EXAMPLE_PATH = ROOT / "providers.example.json"


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    vision_model: str | None = None
    # Not every OpenAI-compatible endpoint implements response_format. Free-tier
    # community models frequently do not, and sending it gets the whole request
    # rejected -- so it is declared per provider rather than assumed.
    supports_json_mode: bool = True
    note: str = ""

    @property
    def has_vision(self) -> bool:
        return bool(self.vision_model)

    @property
    def usable(self) -> bool:
        return bool(self.api_key) and not self.api_key.endswith("_here")


def _from_env() -> Provider:
    s = settings()
    return Provider(
        name="env",
        base_url=s.base_url,
        api_key=s.api_key,
        model=s.model,
        vision_model=s.vision_model,
        supports_json_mode=True,
        note="Loaded from .env (no providers.json present).",
    )


def _load_file() -> dict | None:
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    return None


def load_providers() -> dict[str, Provider]:
    data = _load_file()
    if not data:
        return {"env": _from_env()}

    providers: dict[str, Provider] = {}
    for name, raw in (data.get("providers") or {}).items():
        key = (raw.get("api_key") or "").strip()
        if not key:
            # Blank means "reuse whatever .env has" -- convenient for the provider
            # you already had configured before this file existed.
            key = settings().api_key
        providers[name] = Provider(
            name=name,
            base_url=raw["base_url"],
            api_key=key,
            model=raw["model"],
            vision_model=raw.get("vision_model"),
            supports_json_mode=bool(raw.get("supports_json_mode", True)),
            note=raw.get("note", ""),
        )
    return providers or {"env": _from_env()}


def active_name() -> str:
    data = _load_file()
    if data and data.get("active") in (data.get("providers") or {}):
        return str(data["active"])
    return next(iter(load_providers()))


def active_provider() -> Provider:
    providers = load_providers()
    return providers.get(active_name()) or next(iter(providers.values()))


def set_active(name: str) -> Provider:
    """Switch provider and persist the choice. Creates providers.json if needed."""
    providers = load_providers()
    if name not in providers:
        raise ValueError(f"unknown provider {name!r}; have {sorted(providers)}")

    data = _load_file() or {"providers": {}}
    data["active"] = name
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return providers[name]


def with_model(provider: Provider, model: str) -> Provider:
    return replace(provider, model=model)


def describe() -> list[dict]:
    """For the API/UI: which providers exist and whether they are usable."""
    current = active_name()
    return [
        {
            "name": p.name,
            "active": p.name == current,
            "model": p.model,
            "vision_model": p.vision_model,
            "has_vision": p.has_vision,
            "json_mode": p.supports_json_mode,
            "configured": p.usable,
            "note": p.note,
        }
        for p in load_providers().values()
    ]

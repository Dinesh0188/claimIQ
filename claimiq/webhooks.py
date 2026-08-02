"""Signed outbound callbacks, so a long batch does not have to be polled.

Polling a 4,000-claim run means the client picks an interval, and every interval is
wrong in one direction: too short and it hammers the API for twenty minutes, too long
and the result sits finished for five. A callback moves the decision to the side that
knows when the work is done.

Everything here is about not being a liability to the receiver:

**Signed.** `X-ClaimIQ-Signature: sha256=<hmac>` over the exact bytes sent, keyed by a
per-subscription secret. Without it the receiver has an unauthenticated endpoint that
accepts claim settlements, and anyone who learns the URL can post fabricated ones.

**Timestamped, and the timestamp is inside the signature.** A signature over the body
alone is replayable forever -- an attacker who captures one delivery can resend it
indefinitely. Signing `timestamp.body` and having the receiver reject old timestamps
bounds the window to minutes.

**Retried, with a ceiling.** Receivers restart. A single attempt makes a callback less
reliable than the polling it replaced, and unbounded retries turn one flapping
subscriber into a permanent load. Exponential, five attempts, then dropped and logged.

**Never able to fail the work.** Delivery happens after the batch is complete and its
results are durable. A dead receiver must not cost anyone their audit.

The one thing deliberately not built: SSRF protection beyond a scheme check. Callback
URLs come from configuration -- an operator editing `webhooks.json` -- rather than from
API callers, so the attack requires the privilege it would grant. If subscription ever
becomes a user-facing API call, this needs a resolved-IP denylist for link-local,
loopback and RFC1918 ranges *before* that ships. Written down here because that is
exactly the kind of precondition that gets lost between the two commits.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlparse

import requests

from claimiq.config import ROOT
from claimiq.observability import METRICS, log

CONFIG_PATH = ROOT / "webhooks.json"

# Attempt 1 is immediate; then 2s, 8s, 32s, 128s. Roughly three minutes total, which
# covers a receiver restart without holding a worker for the afternoon.
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 2.0
TIMEOUT_S = 10.0

# Receivers should reject anything older than this. Stated in the docs and enforced by
# them, not by us -- but the constant lives here so both sides quote the same number.
SIGNATURE_TOLERANCE_S = 300


@dataclass(frozen=True)
class Subscription:
    tenant: str
    url: str
    secret: str
    events: frozenset[str]

    @property
    def usable(self) -> bool:
        return bool(self.secret) and urlparse(self.url).scheme in {"http", "https"}


def _load() -> list[Subscription]:
    if not CONFIG_PATH.is_file():
        return []
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log("webhook.config_invalid", error=str(exc))
        return []

    subscriptions = []
    for entry in raw.get("subscriptions", []):
        subscriptions.append(
            Subscription(
                tenant=entry.get("tenant", "local"),
                url=entry.get("url", ""),
                secret=entry.get("secret", ""),
                events=frozenset(entry.get("events", ["batch.completed"])),
            )
        )
    return [s for s in subscriptions if s.usable]


@lru_cache(maxsize=1)
def subscriptions() -> list[Subscription]:
    return _load()


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`sha256=<hex>` over `timestamp.body`.

    The timestamp is part of the signed material rather than merely a header, which is
    what makes it worth anything -- a header outside the signature can be rewritten by
    whoever replays the request.
    """
    material = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver(event: str, payload: dict, tenant: str, session=None) -> list[dict]:
    """Send `event` to every subscription for `tenant`. Returns one result per target.

    Synchronous and blocking, called from a worker thread that has already finished
    the work it is reporting. Making it async would mean a second concurrency model
    for no benefit here -- there are at most a handful of subscribers and nobody is
    waiting on this.
    """
    targets = [s for s in subscriptions() if s.tenant == tenant and event in s.events]
    if not targets:
        return []

    body = json.dumps(
        {
            "event": event,
            "tenant": tenant,
            "sent_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "data": payload,
        },
        default=str,
    ).encode("utf-8")

    return [_deliver_one(target, event, body, session) for target in targets]


def _deliver_one(target: Subscription, event: str, body: bytes, session=None) -> dict:
    post = (session or requests).post
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-ClaimIQ-Event": event,
        "X-ClaimIQ-Timestamp": timestamp,
        "X-ClaimIQ-Signature": sign(target.secret, timestamp, body),
    }

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = post(target.url, data=body, headers=headers, timeout=TIMEOUT_S)
            if 200 <= response.status_code < 300:
                METRICS.inc("claimiq_webhook_delivered_total", {"event": event})
                log("webhook.delivered", event=event, url=target.url, attempt=attempt)
                return {"url": target.url, "ok": True, "attempts": attempt}

            # 4xx other than 429 means the receiver understood and refused. Retrying a
            # request the other side has rejected on its merits just repeats the
            # rejection, so this stops -- 429 and 5xx are the ones worth trying again.
            last_error = f"HTTP {response.status_code}"
            if 400 <= response.status_code < 500 and response.status_code != 429:
                break
        except Exception as exc:  # noqa: BLE001 - network, DNS, TLS: all retryable
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_BASE_S ** attempt)

    METRICS.inc("claimiq_webhook_failed_total", {"event": event})
    log("webhook.failed", event=event, url=target.url, error=last_error)
    return {"url": target.url, "ok": False, "attempts": MAX_ATTEMPTS, "error": last_error}


def deliver_async(event: str, payload: dict, tenant: str) -> None:
    """Fire and forget, on a daemon thread.

    Daemon so a hung receiver cannot keep the process alive at shutdown. The delivery
    is best-effort by design: the durable record is the ledger, and a webhook is a
    notification about it rather than the thing itself.
    """
    if not any(s.tenant == tenant and event in s.events for s in subscriptions()):
        return
    threading.Thread(
        target=deliver, args=(event, payload, tenant), daemon=True, name="claimiq-webhook"
    ).start()

"""Start the FastAPI service in-process on single-port hosts.

Locally you run two processes (`.\\start.ps1`). Streamlit Community Cloud and Hugging
Face Spaces give you one process and one port, so the API would simply not exist and
every page would show "cannot reach the API".

This starts uvicorn on a daemon thread instead. The UI still talks to it over HTTP, so
the API boundary stays genuine -- the two services are merely co-located. Locally this
is a no-op, because the health check finds the already-running server and returns.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = os.environ.get("CLAIMIQ_API", "http://127.0.0.1:8000")
_lock = threading.Lock()
_started = False


def _alive(timeout: float = 1.5) -> bool:
    try:
        return requests.get(f"{API}/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def ensure_api(wait_seconds: float = 45.0) -> bool:
    """Return True once the API answers. Idempotent and safe to call on every rerun."""
    global _started

    if _alive():
        return True

    with _lock:
        if not _started:
            import uvicorn

            def run() -> None:
                uvicorn.run(
                    "claimiq.api:app",
                    host="127.0.0.1",
                    port=8000,
                    log_level="warning",
                )

            threading.Thread(target=run, daemon=True, name="claimiq-api").start()
            _started = True

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _alive():
            return True
        time.sleep(0.5)
    return False

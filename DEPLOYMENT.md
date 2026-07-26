# Deploying ClaimIQ so other people can see it

Goal: a link you can put on a CV that loads in a few seconds and shows a working product.

**Recommended: Streamlit Community Cloud.** Free, no card, connects to a GitHub repo, and
it is built for exactly this shape of app. The rest of this document assumes it.

---

## Before you deploy: the one architectural change

Locally the Streamlit UI talks to FastAPI over HTTP on `127.0.0.1:8000`. Streamlit Cloud
runs **one process** and exposes **one port**, so that second service will not exist and
every page will show "cannot reach the API".

You have two options.

### Option A — run both in one process (keeps the API real)

Add `ui/_boot.py`:

```python
"""Start the FastAPI service in-process when running on a single-port host."""
import os
import threading
import time

import requests
import uvicorn

API = os.environ.get("CLAIMIQ_API", "http://127.0.0.1:8000")


def ensure_api() -> None:
    try:
        requests.get(f"{API}/health", timeout=2)
        return
    except Exception:
        pass

    def run():
        uvicorn.run("claimiq.api:app", host="127.0.0.1", port=8000, log_level="warning")

    threading.Thread(target=run, daemon=True).start()
    for _ in range(40):
        try:
            requests.get(f"{API}/health", timeout=1)
            return
        except Exception:
            time.sleep(0.5)
```

Then call `ensure_api()` at the top of `ui/_shared.py`'s `require_api()`. The API boundary
stays genuine — Streamlit still speaks HTTP to it — you have just co-located the process.

### Option B — import the engine directly

Simpler, but you lose the API boundary that is part of what the project demonstrates. Only
do this if Option A gives you trouble.

**Take Option A.** "The UI never imports the engine" is a real design point and worth
keeping in the deployed version.

---

## Step by step

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "ClaimIQ: agentic pre-submission claim auditor"
git branch -M main
git remote add origin https://github.com/<you>/claimiq.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `providers.json`, `.venv/`, `.cache/` and
`data/generated/`. **Verify no key was committed before pushing:**

```bash
git grep -n "gsk_\|sk-or-v1" -- . ':!*.md'
```

That must return nothing. If a key ever reaches GitHub, rotate it — scrapers find them in
minutes.

### 2. Commit a seeded database

The dashboard needs data, and Streamlit Cloud's disk resets on every restart, so seeding at
runtime is not an option. Commit the SQLite file:

```bash
python scripts/gen_samples.py 300
python scripts/seed_db.py --ai 0     # deterministic only, no API calls, ~30 seconds
git add -f data/claimiq.db
git commit -m "Seed demo portfolio (302 synthetic claims)"
```

`--ai 0` keeps it reproducible and free. The dashboard already discloses the
deterministic/agent split on screen, so this stays honest.

### 3. Create the app

1. Go to <https://share.streamlit.io> and sign in with GitHub
2. **New app** → pick your repo, branch `main`
3. **Main file path:** `ui/app.py`
4. **Advanced settings → Python version:** 3.12
5. Deploy

### 4. Add secrets

In the app's **Settings → Secrets**, paste:

```toml
LLM_BASE_URL = "https://api.groq.com/openai/v1"
LLM_API_KEY = "gsk_your_key"
LLM_MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.6-27b"
AI_ENABLED = "true"
```

Streamlit exposes these as environment variables, which is exactly what `claimiq/config.py`
already reads. No code change needed.

**If you would rather not put a key on a public app**, set `AI_ENABLED = "false"`. Everything
still works — deterministic classification, the full waterfall, all four screens, the
dashboard. Only the generated narrative and the Ask page go quiet. This is a legitimate
choice for a public demo and the app states its own mode in the sidebar.

### 5. First-load timing

`fastembed` downloads a ~130 MB model on first use, so the very first page load after a cold
start takes 1–2 minutes. To avoid a recruiter meeting a spinner, commit the embedding cache:

```bash
python scripts/index_corpus.py
git add -f .cache/embeddings
git commit -m "Commit embedding cache for fast cold start"
```

---

## Cost and limits

| | |
|---|---|
| Streamlit Community Cloud | Free. Sleeps after ~7 days idle, wakes on visit. |
| Groq free tier | ~200k tokens/day, **per organisation** — a second key on the same account shares the pool. |
| One audit | ~2,600–3,900 tokens. Roughly 50–75 full agent runs per day. |
| Disk cache | Repeat audits of the same claim cost nothing. |

Rate limits are the main risk with a public link. The app degrades to deterministic mode
rather than erroring, so a quota exhaustion mid-demo does not produce a broken page.

---

## Alternatives

**Hugging Face Spaces** — also free, also single-port, same Option A change. Slightly more
generous on cold starts. Choose it if you want the app next to an ML portfolio.

**Render / Railway** — proper two-service deploys, so the API and UI stay separate exactly
as they run locally. Free tiers cold-start slowly (30–60 s), which is a poor first
impression for a recruiter clicking a link.

**Docker anywhere** — the project is a plain Python app with no system dependencies beyond
what pip installs. A three-line Dockerfile works if you already have somewhere to run it.

---

## Before you share the link

- [ ] `git grep "gsk_\|sk-or-v1"` returns nothing outside documentation
- [ ] Dashboard shows 302 claims, not an empty state
- [ ] Sidebar shows the correct provider and corpus version
- [ ] Audit page runs `billing_error` and shows the ₹24,300 hospital write-off
- [ ] The "unverified snapshot" and "synthetic data" notices are visible
- [ ] README links to the live URL

That last point matters more than it sounds. The single most effective thing on the page is
the `billing_error` sample, because the hospital write-off number is the finding no
blocklist tool surfaces — lead the demo with it.

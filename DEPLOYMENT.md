# Deploying ClaimIQ

Goal: a public link you can put on a CV that loads quickly and shows a working product.

**Streamlit Community Cloud.** Free, no card, deploys from a GitHub repo.

---

## Already done — you do not need to redo any of this

| | |
|---|---|
| Git repo initialised, 2 commits | `git log --oneline` |
| `ui/_boot.py` written and wired into `require_api()` | starts FastAPI in-process on single-port hosts |
| Seeded database committed | `data/claimiq.db`, 302 claims, 0.96 MB |
| Embedding cache committed | `.cache/embeddings`, avoids a ~2 min cold start |
| Python version pinned | `.python-version` → 3.12 |
| `.gitignore` covers secrets | `.env` and `providers.json` are untracked, verified |
| Local validation passed | 59 tests, invariant, citations, PDF, SQL guardrails |

Both `data/claimiq.db` and `.cache/embeddings` are gitignored by default and were
force-added deliberately. Streamlit Cloud wipes disk on restart, so they must ship with
the repo — seeding at runtime is not an option.

---

## What only you can do — three steps

### 1. Create a GitHub repo and push

Make an **empty** repo at github.com (no README, no .gitignore — the repo already has
both), then:

```bash
git remote add origin https://github.com/<you>/claimiq.git
git push -u origin main
```

Before pushing, confirm no key is in the tree. This must print nothing:

```bash
git grep -nE "gsk_|sk-or-v1-" -- . ":!*.md"
```

If a key ever reaches GitHub, rotate it — scrapers find them within minutes.

### 2. Deploy

1. <https://share.streamlit.io> → sign in with GitHub
2. **New app** → your repo, branch `main`
3. **Main file path:** `ui/app.py`
4. Deploy

Python 3.12 is picked up from `.python-version`; you do not need Advanced settings.

### 3. Add secrets

App → **Settings → Secrets** → paste:

```toml
LLM_BASE_URL = "https://api.groq.com/openai/v1"
LLM_API_KEY  = "gsk_your_key"
LLM_MODEL    = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.6-27b"
AI_ENABLED   = "true"
```

Streamlit exposes these as environment variables, which is what `claimiq/config.py` already
reads. No code change.

**Or deploy with no key at all.** Set `AI_ENABLED = "false"` and every screen still works —
deterministic classification, the full waterfall, the dashboard, the audit PDF. Only the
generated narrative and the Ask page go quiet, and the sidebar states the mode. For a public
link this is a legitimate choice, and it means a stranger clicking your CV cannot burn your
quota.

---

## What will probably go wrong, and what to do

Ordered by likelihood. Each is a real risk, not boilerplate.

**"Cannot reach the ClaimIQ API".** `_boot.py` failed to start uvicorn in-process. It works
locally, but locally it is a no-op because `start.ps1` already started the server — **the
deploy is its first real execution.** Check the app logs for a traceback. Fallback: in
`ui/_shared.py`, have `require_api()` import the engine directly. You lose the API boundary,
which is a genuine design point, so try to fix `_boot` first.

**App exceeds resource limits.** Free tier is ~1 GB RAM. `onnxruntime` + `fastembed` +
`rapidocr` + `pandas` + `plotly` is not obviously under it. RapidOCR is lazy-loaded behind
`lru_cache` and costs nothing until someone uploads a scan — keep it that way. If the app
OOMs, remove `rapidocr-onnxruntime` from `requirements.txt`; scanned-bill upload stops
working and native PDFs are unaffected.

**Slow first load.** The embedding cache is committed, so this should be seconds. If it is
minutes, the cache did not ship — check `git ls-files .cache/embeddings` returns 2 files.

**Dashboard is empty.** `data/claimiq.db` did not ship. Check
`git ls-files data/claimiq.db`.

**Rate limit mid-demo.** Groq free tier is ~200k tokens/day **per organisation** — a second
key on the same account shares the pool. One audit is ~2,400–3,900 tokens, so roughly 50–75
full runs per day. The app degrades to deterministic rather than erroring, and now says so
in a visible warning rather than silently reporting smaller numbers.

---

## Verify the live link

Same checks that passed locally, against the deployed URL:

- [ ] **Audit** → `billing_error` → hospital write-off shows **₹24,300** and the sidebar
      says AI is on. If it shows ₹19,600, the LLM path is falling back — check the warning
      banner and your secrets.
- [ ] Settlement displays as a **range**; corpus version and unverified-snapshot warning visible
- [ ] **Trace** → six nodes, non-zero tokens, citations expand to corpus text
- [ ] **Dashboard** → 302 claims with the provenance line stating the agent/deterministic split
- [ ] **Ask** → "which billing mistake cost the most?" → SQL shown, table renders
- [ ] Audit report PDF downloads and opens
- [ ] Hard-refresh in a private window — first paint under ~15 s
- [ ] Narrow the window to phone width; the Dashboard is the page most likely to break
- [ ] **Deliberately break it:** set `AI_ENABLED = "false"` in secrets, reload, confirm the
      app degrades gracefully instead of erroring. Then set it back. Worth doing on purpose —
      quotas run out, and a broken page in front of a recruiter is the failure that costs you.

Then add the live URL to the top of `README.md` and push.

---

## Cost

| | |
|---|---|
| Streamlit Community Cloud | free; sleeps after ~7 days idle, wakes on visit |
| Groq free tier | ~200k tokens/day per organisation |
| Per audit | ~2,400–3,900 tokens |
| Repeat audits | free — content-hash disk cache |

Nothing here requires a card.

---

## Rollback

Streamlit redeploys on push, so rollback is `git revert <sha> && git push`. The database and
embedding cache are committed artifacts, so reverting restores a known-good dataset too.

---

## Alternatives

**Hugging Face Spaces** — also free, also single-port, same `_boot.py` applies. Choose it if
you want the app sitting next to an ML portfolio.

**Render / Railway** — real two-service deploys, so API and UI stay separate exactly as they
run locally. Free tiers cold-start in 30–60 s, which is a poor first impression for someone
clicking a link once.

**Docker** — no system dependencies beyond pip, so a short Dockerfile works if you already
have somewhere to run it.

---

## Demo tip

Lead with the `billing_error` sample. The **₹24,300 hospital write-off** is the finding no
blocklist tool surfaces, because it requires knowing that List I is the patient's cost while
Lists II/III/IV are the hospital's. That distinction is the entire point of the project —
open with it.

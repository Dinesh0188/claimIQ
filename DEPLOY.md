# Deploying ClaimIQ (React frontend + FastAPI backend)

This covers the two-service deployment: the React frontend (`frontend/`) on **Vercel**,
and the FastAPI backend (`claimiq/`) as a **Docker container** on Render (or any
container host — Fly.io and Railway work the same way with minor UI differences).

For the older single-process Streamlit deployment, see `DEPLOYMENT.md` instead — that
path still works and is unaffected by anything here.

```
 Browser
   │
   ├──► Vercel (frontend/)  — Next.js, static + serverless
   │        calls the backend directly via NEXT_PUBLIC_API_URL
   │
   └──► Render (Dockerfile) — FastAPI engine, corpus, database, ledger
```

The two are deployed and scaled independently. The frontend never runs the engine;
it only calls the backend over HTTPS, exactly like it does locally through the
Next.js proxy.

---

## 0. Test locally first

Do this before touching either platform. If it does not work locally, it will not
work deployed, and you will be debugging two unfamiliar dashboards instead of one
familiar terminal.

**Backend:**

```powershell
.\.venv\Scripts\python.exe -m uvicorn claimiq.api:app --host 127.0.0.1 --port 8000
```

Confirm it answers:

```powershell
curl http://127.0.0.1:8000/health
```

You should see `"status":"ok"` and `"corpus_chunks":104`. If `key_present` is `false`,
check `.env` has a real `LLM_API_KEY` — the app still works either way, just without
AI-assisted reading.

**Frontend**, in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The sidebar should show the corpus version and a
connection status pill. Try **Check a claim** → run one of the built-in samples
(no upload needed) and confirm you get a verdict with figures.

If this works, everything below is just handing the same two processes to a platform.

---

## 1. Backend on Render

### 1a. Push to GitHub

Render deploys from a Git repo. If you have not already:

```bash
git remote add origin https://github.com/<you>/claimiq.git
git push -u origin main
```

Before pushing, confirm no secret is in the tree:

```bash
git grep -nE "gsk_|sk-or-v1-" -- . ":!*.md"
```

This must print nothing. If a key is ever pushed, rotate it immediately — scrapers
find exposed keys within minutes.

### 1b. Deploy the Blueprint

1. <https://dashboard.render.com> → **New** → **Blueprint**
2. Connect the repo. Render finds `render.yaml` at the repo root automatically.
3. It will prompt for the environment variables marked `sync: false`:
   - `LLM_API_KEY` — your Groq/DeepSeek/OpenAI key
   - `CLAIMIQ_CORS_ORIGINS` — leave blank for now, you'll set this after step 2
   - `CLAIMIQ_API_KEYS` — leave blank unless you want authentication on from day one
4. Click **Apply**. First build takes several minutes — `rapidocr-onnxruntime` and
   `fastembed` are the slow installs.
5. Once live, note the service URL: `https://claimiq-api.onrender.com` (yours will
   have a different subdomain, or a custom domain if you configured one).

### 1c. Verify the deployed backend

```bash
curl https://claimiq-api-<yours>.onrender.com/health
```

Same check as local — `status: ok`, corpus chunks present. If this fails, check the
Render service logs; the most common first-deploy issue is a missing `LLM_API_KEY`
(the app still boots without one, `key_present` will just read `false`).

**Free/starter tier note:** these tiers spin down after inactivity. The first request
after idle takes 30–60s to cold-start. This is expected, not a bug — upgrade the plan
if that latency is unacceptable for your use case.

---

## 2. Frontend on Vercel

### 2a. Deploy

1. <https://vercel.com/new> → import the same GitHub repo
2. **Root Directory**: set this to `frontend` — the repo root has both the Python
   backend and the frontend, and Vercel needs to know which subfolder is the Next.js
   app. This is the one setting that is easy to miss.
3. Framework preset should auto-detect as **Next.js**
4. Before deploying, add the environment variable:
   - `NEXT_PUBLIC_API_URL` = `https://claimiq-api-<yours>.onrender.com`
     (the exact URL from step 1c, no trailing slash)
5. Click **Deploy**.

### 2b. Wire up CORS

The frontend and backend are now on different origins, so the backend must explicitly
allow the frontend's origin. Go back to Render:

1. Render dashboard → `claimiq-api` service → **Environment**
2. Set `CLAIMIQ_CORS_ORIGINS` to your Vercel production URL, e.g.
   `https://claimiq.vercel.app`
3. If you want PR preview deployments to work too (each gets its own subdomain like
   `claimiq-git-fix-123-yourteam.vercel.app`), also set:
   ```
   CLAIMIQ_CORS_ORIGIN_REGEX=https://claimiq.*\.vercel\.app
   ```
   Replace `claimiq` with your actual Vercel project slug. This is a regex matched
   against the request's `Origin` header — the exact list above still names your real
   production domain; the regex only covers the preview subdomains a fixed list can't
   keep up with.
4. Save — Render redeploys the service automatically on an env var change.

### 2c. Verify the deployed frontend

Open your Vercel URL. Same checks as local:

- Sidebar shows corpus version and connection status (confirms the frontend can
  reach the backend cross-origin)
- **Check a claim** → run a sample → get a verdict
- Upload a real PDF → confirm extraction and audit both work
- **Claims**, **Dashboard**, **Rule catalog** pages load without errors

If the sidebar shows a connection error, open the browser console — a CORS error
there means step 2b's origin does not exactly match what Vercel actually deployed to
(check for a trailing slash mismatch or `www.` vs bare domain).

---

## 3. Environment variable reference

| Where | Variable | Purpose |
|---|---|---|
| Render | `LLM_API_KEY` | Your LLM provider key. Omit to run rules-only. |
| Render | `LLM_BASE_URL`, `LLM_MODEL`, `VISION_MODEL` | Provider endpoint + models |
| Render | `AI_ENABLED` | `false` disables all LLM calls, deterministic only |
| Render | `CLAIMIQ_CORS_ORIGINS` | Exact frontend origin(s), comma-separated |
| Render | `CLAIMIQ_CORS_ORIGIN_REGEX` | Regex for Vercel preview subdomains (optional) |
| Render | `CLAIMIQ_API_KEYS` | `key:tenant:scopes` entries; blank = no auth |
| Render | `CLAIMIQ_RATE_LIMIT_PER_MINUTE` | Per-principal request budget |
| Vercel | `NEXT_PUBLIC_API_URL` | Absolute backend URL the browser calls directly |

`NEXT_PUBLIC_*` variables are baked into the client bundle at build time and are
visible to anyone who opens dev tools — this is normal for a public API base URL, but
never put a secret in a `NEXT_PUBLIC_*` variable.

---

## 4. What is and is not production-hardened

Read this before pointing either deployment at real patient data.

**Already handled:**
- Money is `Decimal` throughout, never float — no rounding-drift risk from the
  deployment itself
- Uploads are capped and read incrementally (`CLAIMIQ_MAX_UPLOAD_BYTES`), not
  buffered whole before rejecting an oversized file
- CORS is explicit-origin by default; no wildcard is ever used
- The audit ledger (`CLAIMIQ_LEDGER=true`) is append-only and hash-chained

**Not handled by this deployment shape — see `ENTERPRISE.md` for the full list:**
- **No authentication by default.** `CLAIMIQ_API_KEYS` is blank in `render.yaml`,
  which means anyone with the Render URL can call every endpoint. Fine for a personal
  demo; set real keys before sharing the link or pointing at real claims.
- **SQLite, not Postgres.** One writer, one file, no tenant isolation in the schema.
  Adequate for a single-user or small-team demo; a real multi-hospital deployment
  needs the Postgres migration described in `ENTERPRISE.md` §3.
- **No encryption at rest.** Render's disk is not encrypted by this setup. Bills
  carry names, diagnoses and dates — do not put real PHI through this deployment as
  configured.
- **Render's persistent disk is single-instance.** This is fine at the `starter` plan
  (one instance), but the moment you scale to multiple instances, SQLite on a single
  disk stops being correct — see the Tier-1 gaps in `ENTERPRISE.md`.

None of this is a reason not to deploy the demo — it is the reason not to call it done.

---

## 5. Rollback

Both platforms redeploy from Git, so rollback is the same on either side:

```bash
git revert <sha>
git push
```

Render and Vercel both pick up the new commit and redeploy automatically. For a Render
disk-backed service specifically, reverting code does **not** revert the database —
if a bad deploy corrupted data, that needs a manual fix against the disk, not just a
code rollback.

---

## 6. Alternatives to Render

The Dockerfile is platform-agnostic — anything that can run a container from a
Dockerfile works:

- **Fly.io** — `fly launch`, then `fly volumes create` for the same persistent-disk
  need `render.yaml` covers; `fly deploy` picks up the Dockerfile automatically.
- **Railway** — connect the repo, Railway detects the Dockerfile, add the same env
  vars from §3 in its dashboard.
- **A VPS with plain Docker** — `docker build -t claimiq-api .` then
  `docker run -p 8000:8000 --env-file .env -v claimiq-data:/app/data claimiq-api`.

Whichever you choose, the frontend side of this guide (§2) is unchanged — only
`NEXT_PUBLIC_API_URL` needs to point at wherever the backend ends up.

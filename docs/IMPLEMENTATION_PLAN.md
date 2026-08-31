# ClaimIQ — Final Implementation and Deployment Plan

**Status:** Approved implementation plan  
**Prepared:** 31 August 2026  
**Scope:** Finish, verify, and prepare ClaimIQ for a public synthetic-data demo  
**Deployment target:** Next.js frontend on Vercel; FastAPI backend on Render

---

## 1. Goal

Finish ClaimIQ as a polished, secure, accessible, and verifiable portfolio/demo product without expanding into a full enterprise healthcare platform.

The final release must:

- Run the complete claim-audit workflow from the browser.
- Produce exactly reconciling settlement, patient-liability, and hospital-write-off figures.
- Work on desktop and mobile.
- Be keyboard accessible and screen-reader friendly.
- Use a supported, security-patched Next.js release.
- Pass backend, frontend, accessibility, and end-to-end checks.
- Contain only synthetic data.
- Deploy the backend first, followed by the Vercel frontend.

This plan deliberately avoids a PostgreSQL migration, SSO, durable distributed queues, full multi-hospital tenancy, and real-patient-data support. Those belong to a later enterprise roadmap.

---

## 2. Current Baseline

### Working today

- FastAPI audit backend with deterministic money calculations.
- Claim extraction, classification, retrieval, verification, and PDF reporting.
- Claim history and claim-detail APIs.
- Portfolio analytics, recovery modelling, and natural-language Ask flow.
- Batch processing, execution traces, audit ledger, and retention APIs.
- Next.js frontend with 11 routes.
- Frontend TypeScript check passes.
- Frontend production build passes.
- Existing frontend tests pass: 5 tests across 2 test files.
- Existing backend test suite contains 167 test functions.
- Vercel frontend configuration and Render backend configuration already exist.
- GitHub Actions workflow already contains backend and frontend jobs.

### Known blockers and risks

- The local `.venv` points to a missing Python 3.12 runtime.
- The available system Python is 3.14 and does not have the project test dependencies.
- Backend tests therefore need to be re-established locally before release certification.
- The frontend currently uses Next.js 14.2.35 and must be upgraded to a supported security-patched line.
- The dashboard is the largest frontend route at approximately 203 kB first-load JavaScript.
- Frontend coverage is too small for 11 routes and several critical workflows.
- Several form controls and interactions need accessibility corrections.
- The repository currently has no Git remote configured.
- The current branch is `feat/claimiq-completion`; CI push configuration currently names `master`.
- The worktree contains pre-existing changes that must not be overwritten:
  - `.claude/settings.local.json`
  - `data/traces/SYNTHETIC-CARDIAC-001.json`

---

## 3. Required Implementation Order

Complete the phases in this order:

1. Restore a clean local verification baseline.
2. Upgrade the frontend security baseline.
3. Fix accessibility and interaction semantics.
4. Improve responsive behaviour and interface finish.
5. Optimize dashboard and client-side performance.
6. Expand frontend and end-to-end tests.
7. Harden public-demo security and configuration.
8. Reconcile documentation and repository state.
9. Deploy the backend to Render.
10. Deploy the frontend to Vercel.
11. Run the production smoke test.

Do not start deployment until Phases 1–8 are complete.

---

## Phase 1 — Restore the Verification Baseline

### Objective

Make every existing project check reproducible on the local machine before changing application behaviour.

### 1.1 Preserve the existing worktree

Before editing:

```powershell
git status --short
git diff -- .claude/settings.local.json
git diff -- data/traces/SYNTHETIC-CARDIAC-001.json
```

Rules:

- Do not discard either existing modification.
- Do not include them in an unrelated implementation commit.
- Recheck both files before every commit.

### 1.2 Restore Python 3.12

Install or make Python 3.12 available, then recreate the virtual environment.

```powershell
Remove-Item -Recurse -Force .venv
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-api.txt
```

The `.venv` removal must target only the verified project-local `.venv` directory.

### 1.3 Run backend checks

```powershell
$env:AI_ENABLED = "false"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check claimiq tests scripts ui
```

If Ruff is not already installed by the requirements files, add it as an explicit development dependency rather than relying on a global installation.

### 1.4 Run frontend checks

```powershell
Set-Location frontend
npm ci
npm run typecheck
npm test
npm run build
Set-Location ..
```

### 1.5 Align Git and CI branch names

Choose `main` as the final default branch unless the repository host already requires another name.

Update `.github/workflows/ci.yml` so push and pull-request checks use the real default branch.

### Phase 1 acceptance criteria

- Python 3.12 environment works locally.
- All backend tests pass with `AI_ENABLED=false`.
- Ruff passes.
- Frontend type-check passes.
- Frontend tests pass.
- Frontend production build passes.
- The 2 pre-existing modified files remain preserved.
- CI names the actual final default branch.

---

## Phase 2 — Upgrade the Frontend Security Baseline

### Objective

Move away from Next.js 14.2.35 before public deployment.

### Target

Use the current security-patched Next.js 15.5 Maintenance LTS release, with matching React, React DOM, type packages, and `eslint-config-next` versions.

Next.js 15 is preferred for this release because it is a smaller migration than Next.js 16 while still providing a supported security baseline.

### Files

- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/next.config.js`
- `frontend/vitest.config.ts`
- Dynamic route page files under `frontend/src/app/**/[param]/page.tsx`
- Any files identified by the official upgrade codemod

### 2.1 Run an upgrade dry-run

From `frontend/`, use the official Next.js codemod in dry-run or review mode first. Inspect every proposed change before applying it.

### 2.2 Upgrade related packages together

Upgrade these as one controlled dependency change:

- `next`
- `react`
- `react-dom`
- `@types/react`
- `@types/react-dom`
- `eslint-config-next`
- Any directly required peer dependency

Do not use broad force flags to hide peer-dependency problems.

### 2.3 Resolve framework changes

Check specifically for:

- Asynchronous dynamic route parameters.
- App Router typing changes.
- Changed caching defaults.
- Updated lint configuration.
- React 19 compatibility.

### 2.4 Replace obsolete lint setup

Replace the current `next lint` script with a direct ESLint command appropriate for the upgraded version.

Add linting to the normal local verification sequence and keep it in CI.

### 2.5 Remove the Vitest configuration warning

Update the Vitest configuration/module format so the ESM/CommonJS warning no longer appears.

### 2.6 Audit production dependencies

```powershell
npm audit --omit=dev
```

Review every high or critical finding. Do not apply an unreviewed breaking `npm audit fix --force`.

### Phase 2 acceptance criteria

- Frontend uses a supported security-patched Next.js 15.5 release.
- React and React DOM versions are compatible.
- No unresolved peer-dependency warning remains.
- No Vitest configuration warning remains.
- No unresolved critical or high production dependency vulnerability remains.
- Type-check, lint, tests, and build all pass.
- All 11 routes render after the upgrade.

---

## Phase 3 — Accessibility and Interaction Semantics

### Objective

Make all essential workflows usable with keyboard navigation and assistive technology.

### Primary files

- `frontend/src/app/layout.tsx`
- `frontend/src/app/globals.css`
- `frontend/src/components/app-shell.tsx`
- `frontend/src/components/ui/button.tsx`
- `frontend/src/components/ui/toast.tsx`
- `frontend/src/components/profile-selector.tsx`
- `frontend/src/components/claims/claims-list-view.tsx`
- `frontend/src/components/claims/claim-detail-view.tsx`
- `frontend/src/components/rules/rules-catalog-view.tsx`
- `frontend/src/components/ask/ask-view.tsx`
- `frontend/src/components/recovery/recovery-view.tsx`
- `frontend/src/components/batches/batch-list.tsx`
- `frontend/src/components/batches/batch-detail.tsx`
- `frontend/src/components/audit/claim-form.tsx`
- `frontend/src/components/audit/upload-zone.tsx`

### 3.1 Add document-level accessibility support

- Add a “Skip to main content” link that becomes visible on keyboard focus.
- Give the main content container a stable ID.
- Add `color-scheme: dark` to the document.
- Add an appropriate `theme-color` metadata value.
- Ensure headings follow a logical hierarchy on every route.

### 3.2 Correct the mobile navigation drawer

- Use dialog/drawer semantics for the mobile sidebar.
- Label the drawer.
- Trap focus while it is open.
- Close it with Escape.
- Restore focus to the menu button after closing.
- Prevent background content from scrolling or receiving interaction.
- Add `overscroll-behavior: contain`.

### 3.3 Standardize focus styles

- Replace generic `focus:outline-none` usage with visible `focus-visible` treatment.
- Apply the same focus system to buttons, links, inputs, selects, textareas, tabs, expandable rows, and dialog controls.
- Never remove the native outline without a visible replacement.

### 3.4 Label every form control

Add explicit labels or accessible names to:

- Claim search.
- Claim month filter.
- Rule search.
- Ask textarea.
- Recovery annual-volume input.
- API-key input.
- Profile selector.
- Any icon-only file or batch control.

Also add appropriate:

- `name`
- `autocomplete`
- `inputMode`
- `spellCheck`
- `aria-describedby`
- `aria-invalid`

### 3.5 Improve claim-form errors

- Associate each blocker with the relevant field.
- Focus the first invalid field after a blocked submission.
- Use a live region for submission and extraction status.
- Make checkbox labels and controls one clickable target.
- Keep the submit button enabled until the request actually begins when field validation allows submission.

### 3.6 Use links for navigation

Replace programmatic navigation controls with `<Link>` where the action changes routes:

- Claims rows and claim identifiers.
- Batch rows and batch identifiers.
- Back to Claims.
- Back to Batches.
- Open Trace.
- Empty-state “Check a claim” actions.

Do not depend on a clickable `<tr>` for navigation.

### 3.7 Improve icons and announcements

- Mark decorative icons with `aria-hidden="true"`.
- Add `aria-label` to every icon-only action.
- Add a polite live region to normal toasts.
- Use assertive announcements only for urgent blocking errors.
- Label the toast dismiss button.

### 3.8 Respect reduced motion

- Disable or minimize entrance animations when `prefers-reduced-motion: reduce` is active.
- Remove `transition-all`; animate only the required property.
- Make spinners, progress animation, and pulse states motion-safe.

### Phase 3 acceptance criteria

- Every primary workflow can be completed using only the keyboard.
- Every input has an accessible name.
- Every icon-only button has an accessible name.
- Focus is always visibly indicated.
- Toasts and asynchronous progress are announced.
- The mobile drawer manages focus correctly.
- Reduced-motion preferences are respected.
- Navigation uses real links where appropriate.

---

## Phase 4 — Responsive and Visual-Finish Pass

### Objective

Preserve the existing visual identity while making the product feel complete across mobile, tablet, and desktop.

### 4.1 Refine page spacing

- Use smaller content padding on mobile and the current larger padding on desktop.
- Add safe-area padding to full-height mobile surfaces.
- Ensure headers and dialogs do not cover focused controls.

### 4.2 Improve tables on small screens

Apply to Claims, Batches, Ask results, dashboard tables, and audit findings:

- Preserve horizontal scrolling where a table is genuinely useful.
- Keep the most important identifier visible.
- Use tabular numerals for all money and count columns.
- Add sensible minimum widths.
- Consider compact stacked rows below the smallest breakpoint for Claims and Batches.

### 4.3 Handle long content safely

Verify the interface with long:

- Claim IDs.
- Diagnoses.
- Filenames.
- Rule titles and aliases.
- API error messages.
- Generated SQL.
- Provider names.

Use `min-w-0`, wrapping, truncation, line clamping, and scroll containers where appropriate.

### 4.4 Improve dashboard resilience

The dashboard currently performs 4 independent queries. Keep them independent, but add section-level states:

- Summary loading and failure.
- Leakage chart loading and failure.
- Top-items loading and failure.
- Missing-documents loading and failure.
- Retry control for each failed section.

Do not treat a failed request as an empty portfolio.

### 4.5 Improve chart accessibility and responsiveness

- Reduce the fixed Y-axis width/margin on smaller displays.
- Safely truncate or wrap long cause names.
- Do not rely only on red and blue to distinguish bearers.
- Provide a visible or screen-reader-accessible table/list containing the same data.

### 4.6 Standardize interface copy

- Use consistent Title Case for page titles and action labels.
- Replace 3 dots with the single ellipsis character `…`.
- Use specific labels such as “Save API Key,” “Retry Connection,” and “Cancel Batch.”
- Ensure error messages state both the problem and the next action.
- Keep “estimate, not adjudication” and synthetic-data warnings visible where decisions could be misinterpreted.

### Phase 4 acceptance criteria

- No unintended page-level horizontal scrollbar appears.
- All routes work at mobile, tablet, and desktop sizes.
- Long content does not break cards, tables, drawers, or dialogs.
- Dashboard partial failures remain understandable and recoverable.
- Chart information is available without depending only on colour.
- Copy and loading language are consistent.

---

## Phase 5 — Frontend Performance

### Objective

Reduce unnecessary initial JavaScript and keep interactions responsive.

### Primary files

- `frontend/src/app/dashboard/page.tsx`
- `frontend/src/components/dashboard/dashboard-view.tsx`
- `frontend/src/components/claims/claims-list-view.tsx`
- `frontend/src/components/rules/rules-catalog-view.tsx`
- `frontend/src/lib/api.ts`

### 5.1 Split the dashboard chart

- Move the Recharts visualization into a dedicated chart component.
- Load it dynamically.
- Render dashboard summary cards and tables without waiting for the chart bundle.
- Reserve the chart height to avoid layout shift.

If the chart does not justify the dependency after measurement, replace it with a lightweight accessible CSS bar visualization.

### 5.2 Improve query-state persistence

Synchronize meaningful view state with URL query parameters:

- Claims: `q`, `month`, and `page`.
- Rules: `q` and `list`.
- Any user-selected dashboard filter introduced later.

Back, forward, refresh, and shared URLs should preserve the view.

### 5.3 Debounce server-backed search

- Debounce Claims search before requesting the API.
- Reset pagination when the committed search or month changes.
- Keep Rules filtering immediate because the full rule list is already local and small.

### 5.4 Use locale-aware formatting

- Use `Intl.NumberFormat` for rupees, counts, and percentages.
- Use `Intl.DateTimeFormat` for audit dates.
- Remove date-string slicing from rendered UI.

### 5.5 Support request cancellation

Pass an `AbortSignal` through the API layer where practical so abandoned searches and route changes do not continue unnecessary work.

### Phase 5 acceptance criteria

- Dashboard first-load JavaScript is materially lower than the current approximately 203 kB.
- Main audit page does not regress materially from approximately 135 kB.
- Chart loading does not cause layout shift.
- Search remains responsive while typing.
- Browser history restores filters and pagination.
- Currency and dates are locale-aware.

---

## Phase 6 — Test Coverage and Release Gates

### Objective

Cover every routed screen and the critical audit workflow without requiring a paid LLM or external network access.

### 6.1 Expand unit and component tests

Add coverage for:

- API response and error parsing.
- API-key storage behaviour.
- Money, percentage, and date formatting.
- Claim-form blockers and valid submission.
- Extraction-to-packet construction.
- Audit-result rendering and profile switching.
- Money reconciliation in the rendered result.
- Claims filters and pagination.
- Rules search and list filters.
- Recovery validation and calculations.
- Ask success, empty rows, and rejected query states.
- Batch creation, progress, cancellation, and failure states.
- Trace loading and missing-trace state.
- Toast announcements and dismissal.
- ConnectionGuard retry state.

### 6.2 Add accessibility automation

Add an accessibility test tool compatible with the current Vitest and Testing Library setup.

Test at minimum:

- App shell.
- Claim upload and claim form.
- Claims list.
- Dashboard.
- Ask form.
- Rule catalog.
- API-key dialog.

Fail CI on serious automated accessibility violations.

### 6.3 Add Playwright end-to-end smoke tests

Use deterministic mode or API fixtures. Do not require a real LLM key.

Required scenarios:

1. Open the application.
2. Load a synthetic sample.
3. Submit an audit.
4. Verify settlement + patient liability + hospital write-off equals gross bill.
5. Open the persisted claim from Claims.
6. Open the claim trace.
7. Load Dashboard analytics.
8. Search and filter the Rule Catalog.
9. Run a portfolio Ask example.
10. Submit and inspect a small batch.
11. Verify backend-unreachable and retry behaviour.
12. Verify the mobile navigation drawer.
13. Complete the main flow using keyboard navigation.

### 6.4 Update CI

CI should run:

#### Backend job

- Install Python 3.12 dependencies.
- Ruff.
- Backend tests with AI disabled.

#### Frontend job

- `npm ci`
- Lint.
- Type-check.
- Unit/component tests.
- Accessibility tests.
- Production build.
- Deterministic Playwright smoke tests.

### Phase 6 acceptance criteria

- Every routed screen has meaningful automated coverage.
- The primary audit flow has an end-to-end test.
- Frontend tests assert the money invariant.
- Accessibility checks run in CI.
- Tests run offline without an LLM key.
- CI is green on the final release branch.

---

## Phase 7 — Public-Demo Security Hardening

### Objective

Make the deployment safe and honest for synthetic demonstration data.

### 7.1 Confirm data posture

- Keep all bundled claims and documents synthetic.
- Do not upload real medical or insurance records during testing.
- Display a clear “Synthetic demo only” notice in the deployed application.
- Keep the “estimate, not adjudication” notice visible on results and reports.

### 7.2 Update framework security headers

Review `frontend/vercel.json` and add or verify:

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- Clickjacking protection using `frame-ancestors` in CSP and/or the existing frame header.
- A restrictive Content Security Policy tested against Next.js, fonts, and required API connections.
- Permissions Policy for unused browser capabilities.
- An intentional search-engine policy.

If this is a private demo, retain `noindex, nofollow`. If it is meant to be discoverable as a portfolio project, replace the global no-index policy intentionally rather than accidentally.

### 7.3 Improve API-key handling

- Do not put any secret in `NEXT_PUBLIC_*` variables.
- Prefer session-oriented API-key storage for the demo rather than long-lived `localStorage` persistence.
- Never prefill a deployed key into the browser bundle.
- Clear cached authenticated queries when a key changes.
- Show a clear unauthorized state for 401 and 403 responses.

### 7.4 Finalize backend public-bind posture

In `render.yaml` and backend configuration:

- Replace `https://<your-project>.*\.vercel\.app` with the real, narrowly scoped Vercel project pattern.
- Configure the exact production frontend origin.
- Choose one explicit posture:
  - Configure `CLAIMIQ_API_KEYS`; or
  - Enable intentionally insecure synthetic-demo mode and display that status clearly.
- Keep upload-size enforcement enabled.
- Keep ledger and structured logging enabled.
- Ensure errors never reveal filesystem paths, SQL internals, or provider secrets.

### 7.5 Run secret checks

Before every push:

```powershell
git status --short
git ls-files .env providers.json frontend/.env.local
git grep -nE "gsk_|sk-or-v1-|sk-[A-Za-z0-9]{20,}" -- . ":!*.md"
```

Expected result:

- `.env`, `providers.json`, and `frontend/.env.local` are not tracked.
- No real key appears in tracked source.

### Phase 7 acceptance criteria

- No tracked secret exists.
- No real patient data exists.
- Next.js is security patched.
- Security headers are verified.
- API-key behaviour is intentional and documented.
- CORS is limited to exact production and narrow preview origins.
- Public backend posture is explicit.
- Errors do not expose sensitive internals.

---

## Phase 8 — Documentation and Repository Finalization

### Objective

Make the repository match the product that is actually being shipped.

### Files to reconcile

- `README.md`
- `DEPLOY.md`
- `DEPLOYMENT.md`
- `DOCUMENTATION.md`
- `ARCHITECTURE.md`
- `ENTERPRISE.md`
- `project-readiness/PROJECT_COMPLETION.md`
- `frontend/README.md`
- `.env.example`
- `frontend/.env.local.example`
- `render.yaml`
- `.github/workflows/ci.yml`

### Required documentation updates

- State the final Next.js and React versions.
- List all 11 frontend routes.
- State the verified backend and frontend test totals.
- Explain the Vercel frontend plus Render backend architecture.
- Explain that Vercel Root Directory must be `frontend`.
- Document the real CORS sequence.
- Document synthetic-demo limitations.
- Remove stale statements about missing Trace, Ask, Recovery, or Batch screens.
- Remove stale endpoint, test, and route counts.
- Document the chosen default branch.
- Include the final smoke-test procedure.

### Commit structure

Use small, reviewable commits. Suggested sequence:

1. `chore(dev): restore reproducible verification setup`
2. `chore(frontend): upgrade to supported Next.js LTS`
3. `fix(a11y): correct navigation, forms, focus, and announcements`
4. `feat(ui): improve responsive states and dashboard resilience`
5. `perf(ui): defer chart bundle and persist filter state`
6. `test(ui): add route coverage, accessibility, and smoke tests`
7. `security(deploy): harden headers, auth posture, and CORS`
8. `docs: reconcile implementation and deployment guidance`

Before every commit:

```powershell
git diff --check
git status --short
```

### Phase 8 acceptance criteria

- Documentation describes the final code accurately.
- No stale route or test counts remain.
- Existing user-owned changes are not accidentally committed.
- Commit history is reviewable.
- Git remote is configured.
- Final branch is pushed.
- CI is green before deployment begins.

---

## Phase 9 — Deploy the Backend to Render

### Objective

Deploy and verify the FastAPI engine before deploying the browser client.

### 9.1 Pre-deployment checks

```powershell
$env:AI_ENABLED = "false"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check claimiq tests scripts ui
git status --short
```

### 9.2 Render configuration

Deploy the repository root using the existing Docker configuration and `render.yaml`.

Configure:

- `LLM_API_KEY` if AI-assisted behaviour is required.
- `LLM_BASE_URL`.
- `LLM_MODEL`.
- `VISION_MODEL`.
- `AI_ENABLED`.
- `CLAIMIQ_API_KEYS` or explicit synthetic-demo mode.
- `CLAIMIQ_CORS_ORIGINS` after the Vercel URL is known.
- Narrow `CLAIMIQ_CORS_ORIGIN_REGEX` for previews.
- Persistent `/app/data` disk.
- Ledger, logging, upload size, and rate-limit settings.

### 9.3 Backend verification

Verify from the public Render URL:

- `/health` returns success.
- Corpus version and chunk count are correct.
- Deployment security status is expected.
- Sample listing works.
- Deterministic audit works.
- Synthetic upload extraction works.
- PDF report generation works.
- Claims persist.
- Ledger verification works.
- Data survives a service restart or redeploy.
- Cold-start behaviour is understood and acceptable.

### Phase 9 acceptance criteria

- Public backend health check passes.
- Audit and extraction workflows pass using synthetic data.
- Persistence survives restart.
- Security posture is intentional.
- Public backend URL is ready for the frontend environment variable.

---

## Phase 10 — Deploy the Frontend to Vercel

### Objective

Deploy the completed Next.js client and connect it to the verified backend.

### 10.1 Import the repository

In Vercel:

- Import the final Git repository.
- Set **Root Directory** to `frontend`.
- Confirm the Next.js framework preset.
- Use the supported Node version required by the upgraded Next.js release.

### 10.2 Configure environment

Set:

```text
NEXT_PUBLIC_API_URL=https://<render-backend-host>
```

Rules:

- Use HTTPS.
- Do not include a trailing slash.
- Do not place API keys or other secrets in a `NEXT_PUBLIC_*` variable.

### 10.3 Deploy a preview first

- Deploy a Vercel preview.
- Add its origin to the narrow backend preview CORS pattern.
- Run the complete smoke test.
- Fix any production-only issue before promotion.

### 10.4 Configure production CORS

After the final production URL exists, set the backend exact origin:

```text
CLAIMIQ_CORS_ORIGINS=https://<production-project>.vercel.app
```

Redeploy or restart the backend configuration, then verify browser requests from the production origin.

### Phase 10 acceptance criteria

- Vercel build succeeds from `frontend/`.
- Frontend reaches the Render backend over HTTPS.
- Exact production CORS works.
- Preview CORS is narrow.
- No secret appears in the browser bundle.
- Preview passes before production promotion.

---

## Phase 11 — Production Smoke Test

Run every item from the actual Vercel production URL.

### Application shell

- [ ] Page loads without console errors.
- [ ] Backend status becomes healthy.
- [ ] Desktop navigation works.
- [ ] Mobile navigation opens, closes, traps focus, and responds to Escape.
- [ ] Skip link works.
- [ ] Keyboard focus is visible.

### Audit workflow

- [ ] Synthetic samples load.
- [ ] A sample claim can be selected.
- [ ] A synthetic PDF can be uploaded and extracted.
- [ ] A synthetic image/scan can be uploaded and extracted.
- [ ] Claim validation identifies missing required values.
- [ ] Audit completes.
- [ ] Settlement + patient liability + hospital write-off equals gross bill exactly.
- [ ] Findings, citations, document gaps, narrative, and actions render.
- [ ] Profile switching works.
- [ ] PDF report downloads successfully.

### Persistence and analysis

- [ ] New claim appears in Claims.
- [ ] Claim detail opens directly by URL.
- [ ] Trace opens.
- [ ] Dashboard summary loads.
- [ ] Leakage chart or accessible alternative loads.
- [ ] Top billing errors load.
- [ ] Missing-document analytics load.
- [ ] Recovery model works.
- [ ] Rule search and filters work.
- [ ] Ask returns an answer or an actionable provider-disabled state.

### Batch workflow

- [ ] Small synthetic batch submits.
- [ ] Progress updates.
- [ ] Batch detail opens directly by URL.
- [ ] Cancellation works for a cancellable batch.
- [ ] Failure states provide a next action.

### Reliability and security

- [ ] Refreshing every dynamic route works.
- [ ] Back and forward navigation preserve filters.
- [ ] Backend-unreachable screen appears and Retry works.
- [ ] 401 and 403 states are understandable if auth is enabled.
- [ ] Security headers are present.
- [ ] CORS permits only intended origins.
- [ ] No source map, response, or browser storage reveals a server secret.
- [ ] No real patient data is present.
- [ ] Backend data survives restart/redeploy.

### Responsive verification

- [ ] 360 px mobile width.
- [ ] 768 px tablet width.
- [ ] 1280 px desktop width.
- [ ] No accidental page-level horizontal scrolling.
- [ ] Tables, dialogs, charts, and long content remain usable.

---

## 4. Final Release Gate

ClaimIQ is ready for the public demo only when all of the following are true:

- [ ] Python 3.12 backend environment is reproducible.
- [ ] Backend tests and Ruff pass.
- [ ] Frontend lint, type-check, tests, accessibility checks, and build pass.
- [ ] Next.js is on a supported security-patched release.
- [ ] Every routed screen has meaningful automated coverage.
- [ ] The audit invariant is asserted in backend and frontend/end-to-end tests.
- [ ] Primary workflows are keyboard accessible.
- [ ] Mobile layouts are verified.
- [ ] Dashboard bundle size is improved or consciously accepted with measurement.
- [ ] No tracked secret exists.
- [ ] Only synthetic data is used.
- [ ] Public backend posture is explicit.
- [ ] Git remote and final branch are configured.
- [ ] CI is green.
- [ ] Render backend passes its smoke test.
- [ ] Vercel preview passes before production promotion.
- [ ] Vercel production passes the complete smoke test.

---

## 5. Post-Release Roadmap — Not Part of This Finish Pass

Do not block the demo release on these items:

- PostgreSQL migration.
- Multi-hospital tenant isolation at the database layer.
- SSO and enterprise identity.
- Durable Redis/Celery or cloud job queue.
- Encryption-at-rest programme for real PHI.
- Full ledger administration UI.
- Retention administration UI.
- Provider administration UI.
- Production real-patient-data support.
- Next.js 16 migration.
- Major visual rebrand.

These should be planned only after the synthetic demo is deployed, observed, and validated.

---

## 6. Deployment Architecture Summary

```text
User Browser
    |
    +-- Vercel
    |     Next.js application from frontend/
    |     NEXT_PUBLIC_API_URL points to Render
    |
    +-- Render
          FastAPI application from repository root
          Docker runtime
          Persistent /app/data disk
          Claim engine, corpus, reports, analytics, and ledger
```

Deployment sequence:

```text
Local verification
    -> CI green
    -> Render backend
    -> backend health and audit verification
    -> Vercel preview
    -> preview smoke test
    -> exact production CORS
    -> Vercel production
    -> production smoke test
```

This is the required final sequence for the current ClaimIQ architecture.

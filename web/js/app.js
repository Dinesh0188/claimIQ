// Router and shell.
//
// Hash routing, deliberately: the fragment is never sent to the server, so deep links
// work without a server-side catch-all and the static mount stays a static mount.

import { api } from "./api.js";
import { banner, clear, el, toast } from "./ui.js";

import * as audit from "./views/audit.js";
import * as claims from "./views/claims.js";
import * as dashboard from "./views/dashboard.js";
import * as rules from "./views/rules.js";

const NAV = [
  { path: "audit", label: "Check a claim", glyph: "+" },
  { path: "claims", label: "Claims", glyph: "≡" },
  { path: "dashboard", label: "Leakage", glyph: "▤" },
  { path: "rules", label: "Rule catalog", glyph: "§" },
];

const ctx = { profile: "typical", health: null };

/* ------------------------------------------------------------------ shell -- */

function shell() {
  const rail = el("aside", { class: "rail" },
    el("a", { class: "rail-brand", href: "/" },
      el("b", {}, "ClaimIQ"),
      el("span", {}, "Pre-submission audit")),
    el("nav", { id: "nav", "aria-label": "Primary" },
      NAV.map((n) => el("a", { href: `#/${n.path}`, "data-path": n.path },
        el("span", { class: "glyph", "aria-hidden": "true" }, n.glyph), n.label))),
    el("div", { class: "rail-foot", id: "rail-foot" }));

  document.body.append(el("div", { class: "shell" },
    rail,
    el("main", { class: "main", id: "view", tabindex: "-1" })));
}

/** Insurer interpretation is a real decision, not a preference: it changes the
 *  headline number. It lives in the URL so a shared link carries the reading it was
 *  produced under. */
function profileControl(profiles) {
  const sel = el("select", { id: "profile", "aria-label": "Insurer interpretation",
    onchange: () => {
      ctx.profile = sel.value;
      localStorage.setItem("ciq.profile", sel.value);
      route();
    } },
    ...Object.entries(profiles).map(([key, p]) =>
      el("option", { value: key, selected: key === ctx.profile }, p.label)));

  return el("div", { class: "field", style: "margin-top:20px" },
    el("label", { for: "profile" }, "Insurer interpretation"),
    sel,
    el("div", { class: "hint", id: "profile-note" }, profiles[ctx.profile]?.note || ""));
}

async function boot() {
  shell();
  ctx.profile = localStorage.getItem("ciq.profile") || "typical";

  const foot = document.getElementById("rail-foot");
  try {
    ctx.health = await api.get("/health");
    const p = await api.get("/api/profiles");
    document.getElementById("nav").after(profileControl(p));
    foot.append(
      el("div", { class: "row", style: "gap:6px" },
        el("span", { class: `tag ${ctx.health.key_present && ctx.health.ai_enabled ? "tag-ok" : "tag-mute"}` },
          ctx.health.key_present && ctx.health.ai_enabled ? "AI reading on" : "Rules only"),
      ),
      el("div", { style: "margin-top:8px" }, ctx.health.provider),
      el("div", { class: "mono", style: "margin-top:4px" }, ctx.health.corpus_version));
  } catch (err) {
    // The whole app is useless without the API, so this is a full-page stop rather
    // than a toast — but it says what to do, which the old dead ends did not.
    clear(document.getElementById("view")).append(
      el("div", { class: "page-head" }, el("h1", {}, "Cannot reach the ClaimIQ service")),
      banner("error", err.message),
      el("p", { class: "small muted" },
        "Start it with ", el("code", {}, ".\\start.ps1"),
        " , or run ", el("code", {}, "python -m uvicorn claimiq.api:app --port 8000"), "."),
      el("button", { class: "btn btn-primary", style: "margin-top:16px",
        onclick: () => location.reload() }, "Try again"));
    return;
  }

  window.addEventListener("hashchange", route);
  await route();
}

/* ----------------------------------------------------------------- router -- */

async function route() {
  const raw = (location.hash || "#/audit").slice(2);
  const [pathPart, queryPart] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const params = new URLSearchParams(queryPart || "");
  const head = parts[0] || "audit";

  for (const a of document.querySelectorAll("#nav a")) {
    const active = a.dataset.path === head;
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }

  const note = document.getElementById("profile-note");
  const view = document.getElementById("view");
  const request = { parts, params, profile: ctx.profile, health: ctx.health };

  try {
    if (head === "audit") await audit.render(view, request);
    else if (head === "claims") {
      if (parts.length > 1) await claims.renderDetail(view, request);
      else await claims.render(view, request);
    } else if (head === "dashboard") await dashboard.render(view, request);
    else if (head === "rules") await rules.render(view, request);
    else {
      clear(view).append(
        el("div", { class: "page-head" }, el("h1", {}, "Page not found")),
        el("p", { class: "muted" }, `Nothing is routed at #/${pathPart}.`),
        el("a", { class: "btn btn-primary", style: "margin-top:16px", href: "#/audit" },
          "Check a claim"));
    }
  } catch (err) {
    // A view that throws must not leave a blank page with a console message nobody
    // is looking at.
    clear(view).append(
      el("div", { class: "page-head" }, el("h1", {}, "Something went wrong")),
      banner("error", err.message),
      el("button", { class: "btn", style: "margin-top:12px", onclick: () => route() }, "Retry"));
    toast(err.message, "bad", 8000);
  }

  if (note && ctx.health) note.textContent = note.textContent; // keep note in sync
  view.focus({ preventScroll: true });
}

boot();

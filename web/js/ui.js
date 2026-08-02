// DOM and formatting helpers.
//
// No framework, no build step. The repository has no JS toolchain and adding one would
// mean node_modules, a bundler and a build in front of a UI that is a few thousand
// lines of DOM. These primitives are what a framework would have given us here.

/** Element factory. Children may be nodes, strings, or nested arrays. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const frag = (...children) => {
  const f = document.createDocumentFragment();
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    f.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return f;
};

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ---------------------------------------------------------------- money -- */

/** Indian grouping: 1,23,456 not 123,456. A hospital desk reads in lakhs.
 *
 * Mirrors claimiq/money.py `rupees()`, including half-up rounding at the display
 * boundary -- JS Math.round is half-up for positives, and the negative branch is
 * made explicit so a credit note does not round the wrong way. */
export function rupees(value, { paise = false } = {}) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";

  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  const whole = paise ? Math.floor(abs) : Math.round(abs);
  const fraction = paise ? (abs - whole).toFixed(2).slice(1) : "";

  const digits = String(whole);
  let grouped = digits;
  if (digits.length > 3) {
    const tail = digits.slice(-3);
    let head = digits.slice(0, -3);
    const parts = [];
    while (head.length > 2) {
      parts.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) parts.unshift(head);
    grouped = `${parts.join(",")},${tail}`;
  }
  return `${sign}₹${grouped}${fraction}`;
}

/** Compact form for chart axes and dense tables: ₹4.1L, ₹1.2Cr. */
export function rupeesShort(value) {
  const n = Math.abs(Number(value) || 0);
  const sign = Number(value) < 0 ? "-" : "";
  if (n >= 1e7) return `${sign}₹${(n / 1e7).toFixed(n >= 1e8 ? 0 : 1)}Cr`;
  if (n >= 1e5) return `${sign}₹${(n / 1e5).toFixed(n >= 1e6 ? 0 : 1)}L`;
  if (n >= 1e3) return `${sign}₹${(n / 1e3).toFixed(0)}k`;
  return rupees(value);
}

export const num = (v) => Number(v ?? 0) || 0;

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

/* -------------------------------------------------------------- verdict -- */

export const SEVERITY_ORDER = { BLOCKER: 0, WARNING: 1, INFO: 2 };

// A mark, not an emoji: rendered in the monospace face so all three take the same
// width, and always paired with the severity word. Hue alone is unreadable in
// greyscale print and for red-green colour blindness.
export const SEVERITY_TAG = { BLOCKER: "tag-bad", WARNING: "tag-warn", INFO: "tag-mute" };

export const VERDICT = {
  CLEAN: { css: "v-clean", mark: "[OK]", title: "Ready to submit" },
  NEEDS_ATTENTION: { css: "v-attention", mark: "[!]", title: "Needs attention" },
  CANNOT_VERIFY: { css: "v-unverified", mark: "[?]", title: "Could not fully check this claim" },
};

/** Merge the engine's three finding lists into one, ordered by severity then money.
 *
 * The engine returns them separately because three different nodes produce them. A
 * user does not care which node found the problem, only how bad it is. */
export function collectFindings(result) {
  const out = [];
  for (const f of result.findings || []) {
    if (f.classification === "PAYABLE") continue; // absence of a finding, not a finding
    out.push({ ...f, kind: "item", title: f.description, detail: f.reason });
  }
  for (const g of result.document_gaps || []) {
    out.push({ ...g, kind: "document", title: g.name, detail: g.reason });
  }
  for (const c of result.consistency_flags || []) {
    out.push({
      ...c,
      kind: "check",
      title: c.check_id.replace("CHK-", "").replace(/-/g, " ").toLowerCase()
        .replace(/\b\w/g, (m) => m.toUpperCase()),
      detail: c.message,
    });
  }
  return out.sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3) ||
      num(b.impact) - num(a.impact)
  );
}

/* --------------------------------------------------------------- states -- */

export const skeleton = (rows = 4) =>
  el("div", { class: "grid", style: "gap:10px" },
    Array.from({ length: rows }, (_, i) =>
      el("div", { class: "skel", style: `width:${100 - i * 9}%` })));

export function emptyState(title, body, action) {
  return el("div", { class: "state" },
    el("h3", {}, title),
    el("p", {}, body),
    action || null);
}

export function banner(kind, text) {
  return el("div", { class: `banner banner-${kind}` }, text);
}

/* --------------------------------------------------------------- toasts -- */

const toastHost = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
document.addEventListener("DOMContentLoaded", () => document.body.append(toastHost));

export function toast(message, kind = "ok", ms = 4000) {
  const node = el("div", { class: `toast toast-${kind}` }, message);
  toastHost.append(node);
  setTimeout(() => node.remove(), ms);
}

/** Copy text, with the clipboard-API fallback that matters on http:// origins.
 *
 * navigator.clipboard is gated on a secure context, and this app is served over plain
 * http on 127.0.0.1 -- which browsers do treat as secure, but a LAN deployment on a
 * bare IP would not. The textarea path keeps copy working there. */
export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = el("textarea", { style: "position:fixed;opacity:0" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    try { document.execCommand("copy"); } finally { ta.remove(); }
  }
  toast("Copied to clipboard");
}

// The rule catalog, browsable. Every determination the engine makes points here.
//
// A rule source you cannot read is not inspectable, whatever the README claims. This
// screen exists so a billing manager can check what the tool believes before trusting
// a deduction, and so a compliance reviewer can see the attribution and the staleness
// without reading markdown in a repository.

import { api } from "../api.js";
import { banner, clear, el, emptyState, skeleton } from "../ui.js";

const LIST_LABEL = {
  LIST_I_OPTIONAL: "List I — optional items (patient pays)",
  LIST_II_ROOM: "List II — subsumed into room charges (hospital absorbs)",
  LIST_III_PROCEDURE: "List III — subsumed into procedure charges (hospital absorbs)",
  LIST_IV_TREATMENT: "List IV — subsumed into treatment costs (hospital absorbs)",
  POLICY_WORDING: "Policy wording (synthetic, illustrative)",
};

const state = { q: "", list: "" };

export async function render(root, ctx) {
  state.q = ctx.params.get("q") || "";
  state.list = ctx.params.get("list") || "";

  clear(root).append(
    el("div", { class: "page-head" },
      el("h1", {}, "Rule catalog"),
      el("p", {}, "Every deduction cites one of these. Read them before you trust one.")),
    el("div", { id: "rules-meta" }),
    el("div", { class: "card", id: "rules-filter" }),
    el("div", { id: "rules-body" })
  );

  const body = document.getElementById("rules-body");
  body.append(el("div", { class: "card" }, skeleton(8)));

  let meta;
  try {
    meta = await api.get("/api/rules");
  } catch (err) {
    clear(body).append(banner("error", `The rule catalog could not be loaded: ${err.message}`));
    return;
  }

  renderMeta(meta);
  renderFilter(meta);
  await load();
}

function renderMeta(meta) {
  const host = clear(document.getElementById("rules-meta"));
  host.append(el("div", { class: "card" },
    el("div", { class: "row" },
      el("div", {},
        el("div", { class: "small muted" }, "Corpus version"),
        el("div", { class: "mono", style: "font-weight:700" }, meta.corpus_version)),
      el("div", { class: "spacer" }),
      el("div", {},
        el("div", { class: "small muted" }, "Rules"),
        el("div", { style: "font-weight:700" }, String(meta.total)))),
    // The note is the honest half of this screen. It is rendered at full weight
    // rather than tucked into a footnote, because a rule set nobody has verified is
    // the single most important thing a reviewer needs to know about it.
    meta.note ? el("div", { class: "banner banner-warn", style: "margin-top:14px" },
      el("b", {}, "Unverified. "), meta.note) : null,
    meta.verified_on
      ? el("p", { class: "xs muted", style: "margin-top:8px" }, `Verified on ${meta.verified_on}`)
      : el("p", { class: "xs muted", style: "margin-top:8px" },
          "No verification date recorded — nobody has checked these against the issuing "
          + "body's current publication.")));
}

function renderFilter(meta) {
  const host = clear(document.getElementById("rules-filter"));

  const search = el("input", {
    type: "search", id: "rules-q", value: state.q,
    placeholder: "Search item, alias or rule id…",
    oninput: () => { state.q = search.value; sync(); },
  });

  const sel = el("select", { id: "rules-list",
    onchange: () => { state.list = sel.value; sync(); } },
    el("option", { value: "" }, "All lists"),
    ...Object.entries(meta.counts || {}).map(([k, n]) =>
      el("option", { value: k, selected: k === state.list }, `${LIST_LABEL[k] || k} (${n})`)));

  host.append(el("div", { class: "row" },
    el("div", { class: "field", style: "flex:1;min-width:240px" },
      el("label", { for: "rules-q" }, "Search"), search),
    el("div", { class: "field", style: "min-width:260px" },
      el("label", { for: "rules-list" }, "List"), sel)));
}

function sync() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.list) p.set("list", state.list);
  const qs = p.toString();
  history.replaceState(null, "", `#/rules${qs ? `?${qs}` : ""}`);
  load();
}

// Fetched once, filtered in the browser. 104 rules is small enough that a round trip
// per keystroke would be slower and less pleasant than filtering locally.
let allRules = null;

async function load() {
  const body = clear(document.getElementById("rules-body"));

  if (!allRules) {
    body.append(el("div", { class: "card" }, skeleton(8)));
    try {
      allRules = (await api.get("/api/rules", { full: 1 })).rules || [];
    } catch (err) {
      clear(body).append(banner("error", `The rule catalog could not be loaded: ${err.message}`));
      return;
    }
    if (!allRules.length) {
      clear(body).append(emptyState(
        "No rules loaded",
        "The corpus is empty, so the engine has nothing to check a claim against. "
        + "This is a fault, not an empty state — the loader normally refuses to start."));
      return;
    }
    clear(body);
  }

  const q = state.q.trim().toLowerCase();
  const rows = allRules.filter((c) => {
    const listKey = c.list_name || "POLICY_WORDING";
    if (state.list && listKey !== state.list) return false;
    if (!q) return true;
    return (
      c.chunk_id.toLowerCase().includes(q) ||
      c.title.toLowerCase().includes(q) ||
      (c.aliases || []).some((a) => a.toLowerCase().includes(q)) ||
      (c.body || "").toLowerCase().includes(q)
    );
  });

  clear(body);
  if (!rows.length) {
    body.append(emptyState("No rules match", "Try a different item name, alias or rule id."));
    return;
  }

  body.append(el("p", { class: "small muted", style: "margin-bottom:12px" },
    `${rows.length} of ${allRules.length} rules`));

  const list = el("div", { class: "findings" });
  for (const c of rows) list.append(ruleRow(c));
  body.append(list);
}

function ruleRow(c) {
  const listKey = c.list_name || "POLICY_WORDING";
  const bearerTag = c.bearer === "HOSPITAL" ? "tag-bad"
    : c.bearer === "PATIENT" ? "tag-warn" : "tag-mute";

  return el("details", { class: "finding" },
    el("summary", {},
      el("span", { class: "mono xs muted", style: "min-width:70px" }, c.chunk_id),
      el("span", { class: "title" }, c.title),
      c.bearer ? el("span", { class: `tag ${bearerTag}` }, c.bearer) : null,
      el("span", { class: `tag ${c.severity === "BLOCKER" ? "tag-bad"
        : c.severity === "WARNING" ? "tag-warn" : "tag-mute"}` }, c.severity || "INFO")),
    el("div", { class: "body" },
      el("p", { class: "small" }, c.body),
      c.aliases?.length
        ? el("p", { class: "xs muted" }, el("b", {}, "Also billed as: "), c.aliases.join(", "))
        : null,
      el("dl", { class: "kv" },
        el("dt", {}, "List"), el("dd", {}, LIST_LABEL[listKey] || listKey),
        el("dt", {}, "Citation"), el("dd", { class: "cite" }, c.citation || "—"),
        el("dt", {}, "Precision"), el("dd", {},
          c.citation_precision === "item"
            ? "Matched to a specific annexure entry"
            : "List level — this item has not been matched line-by-line against the source"),
        el("dt", {}, "Jurisdiction"), el("dd", {}, c.jurisdiction || "—"))));
}

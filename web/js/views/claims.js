// Claims: every audit this desk has run, searchable, reopenable.
//
// The database held 300+ claims and the product had no way to show one. The analytics
// surface was four aggregate endpoints -- you could see the portfolio leaked money and
// not open the claim it leaked on.

import { api } from "../api.js";
import {
  banner, clear, el, emptyState, fmtDate, num, rupees, skeleton,
} from "../ui.js";

const PAGE = 25;
const state = { q: "", month: "", offset: 0 };

export async function render(root, ctx) {
  state.q = ctx.params.get("q") || "";
  state.month = ctx.params.get("month") || "";
  state.offset = Number(ctx.params.get("offset") || 0);

  clear(root).append(
    el("div", { class: "page-head" },
      el("h1", {}, "Claims"),
      el("p", {}, "Every audit run on this machine. Open one to see what it found.")),
    el("div", { class: "card", id: "claims-filter" }),
    el("div", { id: "claims-body" })
  );

  renderFilter();
  await load();
}

function renderFilter() {
  const host = clear(document.getElementById("claims-filter"));

  const search = el("input", {
    type: "search", id: "claims-q", value: state.q,
    placeholder: "Search claim id or diagnosis…",
    // Debounced: a keystroke per request would hammer the API and race its own
    // responses, so the newest query wins by virtue of being the only one sent.
    oninput: debounce(() => { state.q = search.value; state.offset = 0; sync(); }, 250),
  });

  const monthSel = el("select", { id: "claims-month",
    onchange: () => { state.month = monthSel.value; state.offset = 0; sync(); } },
    el("option", { value: "" }, "All months"));

  host.append(el("div", { class: "row" },
    el("div", { class: "field", style: "flex:1;min-width:240px" },
      el("label", { for: "claims-q" }, "Search"), search),
    el("div", { class: "field", style: "min-width:170px" },
      el("label", { for: "claims-month" }, "Month"), monthSel),
    el("div", { class: "spacer" }),
    el("a", { class: "btn btn-primary", href: "#/audit" }, "Check a new claim")));

  host.dataset.monthSelect = "1";
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Push filter state into the URL so a search is shareable and survives a refresh. */
function sync() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.month) p.set("month", state.month);
  if (state.offset) p.set("offset", state.offset);
  const qs = p.toString();
  history.replaceState(null, "", `#/claims${qs ? `?${qs}` : ""}`);
  load();
}

async function load() {
  const host = clear(document.getElementById("claims-body"));
  host.append(el("div", { class: "card" }, skeleton(6)));

  let data;
  try {
    data = await api.get("/api/claims", {
      q: state.q, month: state.month, limit: PAGE, offset: state.offset,
    });
  } catch (err) {
    clear(host).append(banner("error", `Claims could not be loaded: ${err.message}`));
    return;
  }

  // Populate the month filter once we know which months exist.
  const sel = document.getElementById("claims-month");
  if (sel && sel.options.length === 1) {
    for (const m of data.months || []) {
      sel.append(el("option", { value: m, selected: m === state.month }, m));
    }
  }

  clear(host);

  if (!data.total) {
    host.append(emptyState(
      state.q || state.month ? "No claims match that search" : "No claims audited yet",
      state.q || state.month
        ? "Try a different claim id, diagnosis or month."
        : "Once you check a claim it is recorded here, so you can reopen it and compare against the deduction sheet the TPA sends back.",
      state.q || state.month
        ? el("button", { class: "btn", onclick: () => {
            state.q = ""; state.month = ""; state.offset = 0;
            document.getElementById("claims-q").value = "";
            document.getElementById("claims-month").value = "";
            sync();
          } }, "Clear filters")
        : el("a", { class: "btn btn-primary", href: "#/audit" }, "Check a claim")));
    return;
  }

  const rows = data.claims.map((c) => {
    const leak = num(c.hospital_writeoff);
    return el("tr", { class: "clickable", tabindex: "0",
      onclick: () => open(c.claim_id),
      onkeydown: (e) => { if (e.key === "Enter") open(c.claim_id); } },
      el("td", { class: "mono xs" }, c.claim_id),
      el("td", {}, c.diagnosis || "—",
        c.procedure ? el("div", { class: "xs muted" }, c.procedure) : null),
      el("td", { class: "muted xs" }, fmtDate(c.audited_at)),
      el("td", { class: "num" }, rupees(c.gross_bill)),
      el("td", { class: "num" }, rupees(c.settlement)),
      el("td", { class: "num", style: leak ? "color:var(--hospital);font-weight:700" : "" },
        leak ? rupees(leak) : "—"),
      el("td", {},
        c.doc_gap_count ? el("span", { class: "tag tag-bad" }, `${c.doc_gap_count} gaps`) : null,
        c.unmapped_count ? el("span", { class: "tag tag-warn" }, `${c.unmapped_count} unmapped`) : null,
        !c.doc_gap_count && !c.unmapped_count ? el("span", { class: "tag tag-ok" }, "clean") : null),
      el("td", { class: "xs muted" }, c.ai_pipeline ? "AI" : "rules"));
  });

  host.append(
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Claim"), el("th", {}, "Diagnosis"), el("th", {}, "Audited"),
        el("th", { class: "num" }, "Gross"), el("th", { class: "num" }, "Settlement"),
        el("th", { class: "num" }, "Hospital absorbs"), el("th", {}, "Flags"),
        el("th", {}, "Path"))),
      el("tbody", {}, rows))),
    pager(data)
  );
}

function pager(data) {
  const from = data.offset + 1;
  const to = Math.min(data.offset + data.claims.length, data.total);
  return el("div", { class: "row", style: "margin-top:16px" },
    el("span", { class: "small muted" }, `${from}–${to} of ${data.total}`),
    el("div", { class: "spacer" }),
    el("button", { class: "btn btn-sm", disabled: data.offset === 0,
      onclick: () => { state.offset = Math.max(0, state.offset - PAGE); sync(); } }, "Previous"),
    el("button", { class: "btn btn-sm", disabled: to >= data.total,
      onclick: () => { state.offset = state.offset + PAGE; sync(); } }, "Next"));
}

const open = (id) => { location.hash = `#/claims/${encodeURIComponent(id)}`; };

/* ------------------------------------------------------------ one claim -- */

export async function renderDetail(root, ctx) {
  const id = ctx.parts[1];
  clear(root).append(
    el("div", { class: "page-head" },
      el("div", { class: "row" },
        el("a", { class: "btn btn-sm btn-ghost", href: "#/claims" }, "← All claims")),
      el("h1", { style: "margin-top:12px" }, id),
      el("p", {}, "Stored audit. Figures are as recorded when this claim was checked.")),
    el("div", { id: "claim-body" })
  );

  const host = document.getElementById("claim-body");
  host.append(el("div", { class: "card" }, skeleton(5)));

  let c;
  try {
    c = await api.get(`/api/claims/${encodeURIComponent(id)}`);
  } catch (err) {
    clear(host).append(
      banner("error", `That claim could not be loaded: ${err.message}`),
      el("p", { class: "small muted", style: "margin-top:8px" },
        "It may have been removed, or the database rebuilt since it was audited."),
      el("a", { class: "btn", style: "margin-top:12px", href: "#/claims" }, "Back to claims"));
    return;
  }

  const deducted = c.findings.filter((f) => num(f.deducted_amount) > 0);
  const gross = num(c.gross_bill);
  const pct = (x) => (gross > 0 ? (num(x) / gross) * 100 : 0);

  clear(host).append(
    el("div", { class: "figs" },
      fig("Hospital absorbs", c.hospital_writeoff, "fig-loss"),
      fig("Settlement", c.settlement),
      fig("Patient pays", c.patient_liability),
      fig("Gross bill", c.gross_bill)),

    el("div", { class: "card" },
      el("div", { class: "split" },
        el("span", { class: "s-settled", style: `width:${pct(c.settlement)}%` }),
        el("span", { class: "s-patient", style: `width:${pct(c.patient_liability)}%` }),
        el("span", { class: "s-hospital", style: `width:${pct(c.hospital_writeoff)}%` })),
      el("div", { class: "split-key small muted" },
        el("span", {}, el("i", { style: "background:var(--settled)" }), "Insurer settles"),
        el("span", {}, el("i", { style: "background:var(--patient)" }), "Patient pays"),
        el("span", {}, el("i", { style: "background:var(--hospital)" }), "Hospital absorbs")),
      el("div", { class: "row xs muted", style: "margin-top:14px" },
        el("span", {}, `Audited ${fmtDate(c.audited_at)}`),
        el("span", {}, `Month ${c.month}`),
        el("span", { class: "mono" }, c.corpus_version),
        el("span", {}, c.ai_pipeline ? "AI pipeline" : "Deterministic rules"))),

    c.document_gaps.length
      ? el("div", { class: "card" },
          el("div", { class: "card-head" }, el("h2", {}, `Missing documents (${c.document_gaps.length})`)),
          el("div", { class: "findings" }, c.document_gaps.map((g) =>
            el("div", { class: "filerow" },
              el("span", { class: `tag ${g.severity === "BLOCKER" ? "tag-bad" : g.severity === "WARNING" ? "tag-warn" : "tag-mute"}` }, g.severity),
              el("span", { class: "name" }, g.name),
              el("span", { class: "mono xs muted" }, g.document_id)))))
      : null,

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("h2", {}, `Deductions (${deducted.length})`),
        el("span", { class: "small muted" }, "Stored line-item findings")),
      deducted.length
        ? el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "#"), el("th", {}, "Item"), el("th", {}, "Verdict"),
              el("th", {}, "Falls on"), el("th", { class: "num" }, "Deducted"),
              el("th", {}, "Rule"))),
            el("tbody", {}, deducted.map((f) => el("tr", {},
              el("td", { class: "muted" }, f.line_no),
              el("td", {}, f.description),
              el("td", {}, f.classification.replace("LIST_", "List ").replace(/_/g, " ")),
              el("td", { class: "muted" }, f.bearer),
              el("td", { class: "num" }, rupees(f.deducted_amount)),
              el("td", { class: "mono xs muted" }, f.cited_chunk_id || "—"))))))
        : emptyState("Nothing was deducted",
            "Every line on this bill was either a primary service or matched no non-payable rule."))
  );
}

const fig = (label, value, extra = "") =>
  el("div", { class: `fig ${extra}` },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, rupees(value)));

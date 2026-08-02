// Leakage across the whole book, not one bill at a time.
//
// Charts are inline SVG rather than a charting library: three bar charts do not
// justify a 300 kB dependency, and hand-built SVG inherits the app's own colours and
// prints correctly.

import { api } from "../api.js";
import {
  banner, clear, el, emptyState, num, rupees, rupeesShort, skeleton,
} from "../ui.js";

export async function render(root) {
  clear(root).append(
    el("div", { class: "page-head" },
      el("h1", {}, "Where the money goes"),
      el("p", {}, "Across every claim audited, not one bill at a time.")),
    el("div", { id: "dash-body" })
  );

  const host = document.getElementById("dash-body");
  host.append(el("div", { class: "card" }, skeleton(6)));

  let summary;
  try {
    summary = await api.get("/api/analytics/summary");
  } catch (err) {
    clear(host).append(banner("error", `The portfolio summary could not be loaded: ${err.message}`));
    return;
  }

  if (summary.empty) {
    clear(host).append(emptyState(
      "No claims audited yet",
      "Once you check a claim it is recorded here, and this page starts showing which billing mistakes cost the most across your whole book.",
      el("a", { class: "btn btn-primary", href: "#/audit" }, "Check a claim")));
    return;
  }

  clear(host).append(
    el("p", { class: "small muted", style: "margin-bottom:16px" },
      `${summary.claims} claims · ${summary.ai_claims} read by AI, `,
      `${summary.claims - summary.ai_claims} by deterministic rules · synthetic data`),

    el("div", { class: "figs" },
      stat("Claims audited", String(summary.claims)),
      stat("Gross billed", rupees(summary.gross)),
      stat("Preventable hospital loss", rupees(summary.preventable), "fig-loss",
        "Lists II/III/IV — recoverable by re-billing, not by chasing the insurer"),
      stat("Average deduction", `${Number(summary.avg_deduction_pct).toFixed(1)}%`)),

    el("div", { class: "figs", style: "margin-top:12px" },
      stat("Estimated settled", rupees(summary.settlement)),
      stat("Patient liability", rupees(summary.patient)),
      stat("Room-rent deductions", rupees(summary.room_rent_deduction)),
      stat("Document gaps", String(summary.doc_gaps))),

    el("div", { id: "recovery-card" }),

    el("div", { class: "grid g2", style: "margin-top:16px;align-items:start" },
      el("div", { id: "leak-card" }),
      el("div", { id: "docs-card" })),
    el("div", { id: "items-card" })
  );

  loadRecovery();
  loadLeakage();
  loadDocs();
  loadItems();
}

function stat(label, value, extra = "", sub = "") {
  return el("div", { class: `fig ${extra}` },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    sub ? el("div", { class: "sub" }, sub) : null);
}

/** Each panel loads independently. One failing endpoint costs one panel, not the page. */
async function panel(id, title, subtitle, fetcher, draw, emptyText) {
  const host = clear(document.getElementById(id));
  host.className = "card";
  host.append(
    el("div", { class: "card-head" },
      el("h2", {}, title),
      subtitle ? el("span", { class: "small muted" }, subtitle) : null),
    el("div", { class: "body" }, skeleton(4)));

  try {
    const data = await fetcher();
    const body = host.querySelector(".body");
    clear(body);
    if (!data || !data.length) body.append(el("p", { class: "small muted" }, emptyText));
    else body.append(draw(data));
  } catch (err) {
    clear(host.querySelector(".body")).append(
      banner("error", `Could not load: ${err.message}`));
  }
}

/* -------------------------------------------------------------- recovery -- */

/** The business case, not another chart.
 *
 *  Every other panel here reports what happened. This one says what fixing it is
 *  worth per year and which three lines to fix first, which is the only form in which
 *  any of this reaches a finance meeting. The volume assumption is printed next to the
 *  number rather than in a tooltip -- it is the thing the projection rests on, and a
 *  reader who cannot see it is being asked to take the headline on trust.
 */
async function loadRecovery() {
  const host = clear(document.getElementById("recovery-card"));
  host.className = "card card-accent";
  host.style.marginTop = "16px";
  host.append(
    el("div", { class: "card-head" },
      el("h2", {}, "What fixing this is worth"),
      el("span", { class: "small muted" }, "Recoverable by re-billing, projected annually")),
    el("div", { class: "body" }, skeleton(4)));

  let m;
  try {
    m = await api.get("/api/analytics/recovery", { limit: 8 });
  } catch (err) {
    clear(host.querySelector(".body")).append(banner("error", `Could not load: ${err.message}`));
    return;
  }

  const body = clear(host.querySelector(".body"));
  if (m.empty) {
    body.append(el("p", { class: "small muted" }, m.reason));
    return;
  }

  const volumeInput = el("input", {
    type: "number", min: "1", step: "1", id: "vol",
    placeholder: String(m.annual_claim_volume),
    style: "width:120px",
    onkeydown: (e) => { if (e.key === "Enter") reload(); },
  });
  const reload = async () => {
    const v = Number(volumeInput.value) || 0;
    // Re-fetch rather than scaling in the browser: the server owns the formula, and a
    // second implementation here is a second thing to get wrong.
    const next = await api.get("/api/analytics/recovery", { limit: 8, annual_claim_volume: v });
    Object.assign(m, next);
    draw();
  };

  const figures = el("div", { class: "figs" });
  const table = el("div", { class: "table-wrap" });
  const notes = el("div", {});

  function draw() {
    clear(figures).append(
      stat("Recoverable per year", rupees(m.annual_recovery), "fig-loss",
        `at ${Number(m.annual_claim_volume).toLocaleString("en-IN")} claims/year`),
      stat("Top 3 fixes alone", rupees(m.top_three_recovery), "fig-loss",
        "the shortlist that actually gets done"),
      stat("Leak per claim", rupees(m.leak_per_claim),
        "", `${m.leak_rate_pct}% of gross billed`),
      stat("Observed", `${m.claims_audited} claims`,
        "", `over ${m.months_observed} month(s)`));

    clear(table).append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Charge-master line"),
        el("th", { class: "num" }, "On % of claims"),
        el("th", { class: "num" }, "Avg when it appears"),
        el("th", { class: "num" }, "Recoverable / year"),
        el("th", {}, "Rule"))),
      el("tbody", {}, m.items.map((i, n) => el("tr", {},
        el("td", {},
          // Rank badge: the list is an ordered remediation plan, and numbering it is
          // what turns a table into one.
          el("span", { class: "rank" }, String(n + 1)), i.item),
        el("td", { class: "num" }, `${i.incidence_pct}%`),
        el("td", { class: "num" }, rupees(i.avg_per_occurrence)),
        el("td", { class: "num", style: "color:var(--hospital);font-weight:700" },
          rupees(i.annual_recovery)),
        el("td", { class: "xs muted mono" }, i.cited_rule || "—"))))));

    clear(notes).append(
      el("p", { class: "xs muted", style: "margin-top:14px" }, m.volume_source),
      el("ul", { class: "xs muted", style: "margin-top:8px;padding-left:18px" },
        m.assumptions.map((a) => el("li", { style: "margin-top:4px" }, a))));
  }

  draw();
  body.append(
    figures,
    el("div", { class: "row", style: "gap:8px;align-items:end;margin:16px 0 4px" },
      el("div", { class: "field", style: "margin:0" },
        el("label", { for: "vol" }, "Your annual claim volume"),
        volumeInput),
      el("button", { class: "btn", onclick: reload }, "Recalculate")),
    table,
    notes);
}

const loadLeakage = () => panel(
  "leak-card", "Where the money goes", "By who absorbs it",
  () => api.get("/api/analytics/leakage"),
  (rows) => {
    const max = Math.max(...rows.map((r) => num(r.amount)), 1);
    return el("div", {},
      el("div", { class: "bars" }, rows.map((r) => el("div", { class: "bar-row" },
        el("div", {},
          el("div", { class: "bar-label" }, r.cause),
          el("div", { class: "bar-track" },
            el("div", { class: "bar-fill", style:
              `width:${(num(r.amount) / max) * 100}%;background:${
                r.bearer === "HOSPITAL" ? "var(--hospital)" : "var(--patient)"}` }))),
        el("div", { class: "num small" }, rupeesShort(r.amount))))),
      el("p", { class: "xs muted", style: "margin-top:14px" },
        "Bars in red are the hospital's own money — billing errors that repeat on every ",
        "claim. Bars in blue are borne by patients under policy terms."));
  },
  "No deductions recorded yet across the portfolio.");

const loadDocs = () => panel(
  "docs-card", "Most-missed documents", "",
  () => api.get("/api/analytics/missing-docs", { limit: 8 }),
  (rows) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Document"), el("th", {}, "Severity"), el("th", { class: "num" }, "Claims"))),
    el("tbody", {}, rows.map((d) => el("tr", {},
      el("td", {}, d.name),
      el("td", {}, el("span", { class: `tag ${
        d.severity === "BLOCKER" ? "tag-bad" : d.severity === "WARNING" ? "tag-warn" : "tag-mute"
      }` }, d.severity)),
      el("td", { class: "num" }, d.claims)))))),
  "No document gaps recorded — every audited claim arrived complete.");

const loadItems = () => panel(
  "items-card", "Top billing mistakes by aggregate loss",
  "Fixing the top three at the billing-master level stops the leak at source",
  () => api.get("/api/analytics/top-items", { limit: 10 }),
  (rows) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Item as billed"),
      el("th", { class: "num" }, "Claims affected"),
      el("th", { class: "num" }, "Total written off"))),
    el("tbody", {}, rows.map((r) => el("tr", {},
      el("td", {}, r.item),
      el("td", { class: "num" }, r.claims),
      el("td", { class: "num", style: "color:var(--hospital);font-weight:700" },
        rupees(r.total_deducted))))))),
  "No hospital-borne deductions recorded yet.");

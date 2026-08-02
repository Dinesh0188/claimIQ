// Audit: upload a packet, confirm what was read, see what will be deducted.

import { api, rule } from "../api.js";
import {
  VERDICT, banner, clear, collectFindings, copy, el, emptyState, num,
  rupees, SEVERITY_TAG, toast,
} from "../ui.js";

const state = {
  uploads: [],
  result: null,
  packet: null,
  profile: "typical",
  busy: false,
};

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.tiff,.tif,.bmp";
const today = () => new Date().toISOString().slice(0, 10);
const shiftDays = (iso, days) => {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

/* ------------------------------------------------------------- rendering -- */

export async function render(root, ctx) {
  state.profile = ctx.profile || "typical";
  clear(root);
  root.append(
    el("div", { class: "page-head" },
      el("h1", {}, "Check a claim"),
      el("p", {}, "Read the packet before it goes to the TPA.")),
    el("div", { id: "audit-input" }),
    el("div", { id: "audit-result" })
  );
  renderInput();
  renderResult();
}

function renderInput() {
  const host = clear(document.getElementById("audit-input"));

  const input = el("input", {
    type: "file", accept: ACCEPT, multiple: true, class: "hide",
    onchange: (e) => readFiles([...e.target.files]),
  });

  const drop = el("div", {
    class: "drop", tabindex: "0", role: "button",
    "aria-label": "Upload claim documents",
    onclick: () => input.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
    ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      readFiles([...e.dataTransfer.files]);
    },
  },
    el("h3", {}, "Drop the claim packet here"),
    el("p", {}, "Bill, discharge summary, claim form, policy schedule, reports. " +
      "Native PDFs are parsed exactly; scans and photos are read with on-device OCR."));

  host.append(el("div", { class: "card" }, drop, input, el("div", { id: "filelist" })));

  if (state.uploads.length) host.append(buildForm());
  else host.append(samplesCard());
  renderFiles();
}

function samplesCard() {
  const labels = {
    billing_error: "Billing errors", cardiac: "Room-rent trap",
    clean: "Clean claim", incomplete: "Missing documents",
  };
  const row = el("div", { class: "row" });
  const card = el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", {}, "No documents to hand?"),
      el("span", { class: "small muted" }, "Audit a worked example")),
    row);

  api.get("/api/samples").then((names) => {
    for (const name of names) {
      row.append(el("button", {
        class: "btn", onclick: () => auditSample(name),
      }, labels[name] || name.replace(/_/g, " ")));
    }
  }).catch((err) => row.append(banner("error", `Examples unavailable: ${err.message}`)));

  return card;
}

function renderFiles() {
  const host = clear(document.getElementById("filelist"));
  if (!state.uploads.length) return;
  host.className = "filelist";
  for (const u of state.uploads) {
    const rows = u.line_items?.length || 0;
    host.append(el("div", { class: "filerow" },
      el("span", { class: "name" }, u.filename),
      el("span", { class: "tag tag-accent" }, u.file_kind),
      el("span", { class: "small muted" },
        rows ? `${rows} line items · ${u.how}` : u.document_label),
      u.low_confidence
        ? el("span", { class: "tag tag-warn" }, `${u.low_confidence} low confidence`)
        : null));
  }
}

async function readFiles(files) {
  if (!files.length || state.busy) return;
  state.busy = true;
  const host = document.getElementById("filelist");
  clear(host).append(el("div", { class: "row small muted" },
    el("span", { class: "spinner" }), `Reading ${files.length} document(s)…`));

  const summaries = [], failures = [];
  for (const file of files) {
    try {
      summaries.push(await api.upload("/api/extract", file));
    } catch (err) {
      failures.push(`${file.name}: ${err.message}`);
    }
  }
  state.busy = false;
  state.uploads = summaries;
  state.result = null;
  renderInput();
  renderResult();
  for (const f of failures) toast(f, "bad", 9000);
}

/* ------------------------------------------------------------------ form -- */

function buildForm() {
  const uploads = state.uploads;
  const lineItems = uploads.flatMap((u) => u.line_items || []);
  const attached = uploads.map((u) => u.document_id).filter(Boolean);
  const policy = uploads.find((u) => u.policy_terms)?.policy_terms || null;
  const clinical = uploads.find((u) => u.clinical_context)?.clinical_context || null;
  const stay = uploads.find((u) => u.room_stay)?.room_stay || null;
  const hasImplant = uploads.some((u) => u.has_implant);

  const fromDoc = (obj, key, fallback) => {
    const raw = obj?.[key];
    const n = Number(raw);
    return raw != null && raw !== "" && Number.isFinite(n)
      ? { value: n, sourced: true } : { value: fallback, sourced: false };
  };

  const si = fromDoc(policy, "sum_insured", 500000);
  const cap = fromDoc(policy, "room_rent_cap_per_day", 6000);
  const copay = fromDoc(policy, "copay_percent", 10);

  const days = Number(stay?.days) || 0;
  const discharge = clinical?.discharge_date || today();
  const admission = clinical?.admission_date || shiftDays(discharge, -Math.max(days, 1));

  // Every input is named so the packet is read straight off the form on submit --
  // no shadow state to drift out of sync with what the user is looking at.
  const f = (name, label, attrs = {}, sourced = false, hint = "") =>
    el("div", { class: "field" },
      el("label", { for: `f-${name}` }, label,
        sourced ? el("span", { class: "from-doc" }, "  ✓ from document") : null),
      el("input", { id: `f-${name}`, name, ...attrs }),
      hint ? el("div", { class: "hint" }, hint) : null);

  const form = el("form", { id: "packet-form", onsubmit: (e) => { e.preventDefault(); runAudit(); } },
    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("h2", {}, "Policy terms"),
        el("span", { class: "small muted" },
          policy ? "Read from your policy schedule — correct anything out of date."
                 : "No policy schedule uploaded, so these are defaults.")),
      el("div", { class: "grid g4" },
        f("sum_insured", "Sum insured (₹)", { type: "number", min: 0, step: 10000, value: si.value }, si.sourced),
        f("room_cap", "Room limit (₹/day)", { type: "number", min: 0, step: 500, value: cap.value }, cap.sourced, "0 = no room limit"),
        f("copay", "Co-pay (%)", { type: "number", min: 0, max: 100, step: "0.5", value: copay.value }, copay.sourced),
        el("div", { class: "field" },
          el("label", { for: "f-claim_type" }, "Claim type"),
          el("select", { id: "f-claim_type", name: "claim_type" },
            el("option", { value: "cashless" }, "Cashless"),
            el("option", { value: "reimbursement" }, "Reimbursement"))))),

    el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("h2", {}, "Stay and clinical details"),
        el("span", { class: "small muted" },
          stay ? "Room read from the bill." : "No room charge found — enter the stay by hand.")),
      el("div", { class: "grid g4" },
        f("room_category", "Room category", { value: stay?.room_category || "", placeholder: "Single AC / ICU" }, !!stay),
        f("room_rate", "Room rate (₹/day)", { type: "number", min: 0, step: 100, value: Math.round(num(stay?.rate_per_day)) }, !!stay),
        f("room_days", "Room days", { type: "number", min: 0, max: 365, value: days }, !!stay),
        el("div", { class: "field" },
          el("label", { for: "f-is_icu" }, "ICU stay"),
          el("div", { class: "row" },
            el("input", { id: "f-is_icu", name: "is_icu", type: "checkbox", checked: !!stay?.is_icu, style: "width:auto" }),
            el("span", { class: "small muted" }, "Applies the ICU limit")))),
      el("div", { class: "grid g4", style: "margin-top:16px" },
        f("admission", "Admission", { type: "date", value: admission }, !!clinical?.admission_date),
        f("discharge", "Discharge", { type: "date", value: discharge }, !!clinical?.discharge_date),
        f("diagnosis", "Primary diagnosis", { value: clinical?.primary_diagnosis || "", placeholder: "Required" }, !!clinical?.primary_diagnosis),
        f("procedure", "Procedure", { value: clinical?.procedure_performed || "", placeholder: "Blank if medical" }, !!clinical?.procedure_performed)),
      el("div", { class: "grid g4", style: "margin-top:16px" },
        f("preauth", "Pre-authorised (₹)", { type: "number", min: 0, step: 10000, value: 0 }, false, "0 = none issued"),
        el("div", { class: "field" },
          el("label", { for: "f-implant" }, "Implant used"),
          el("div", { class: "row" },
            el("input", { id: "f-implant", name: "implant", type: "checkbox", checked: hasImplant, style: "width:auto" }),
            el("span", { class: "small muted" }, "Drives the invoice requirement"))))),

    el("div", { class: "card" },
      el("div", { id: "blockers" }),
      el("div", { class: "row" },
        el("button", { class: "btn btn-primary", type: "submit", id: "run-btn" }, "Run audit"),
        el("span", { class: "small muted" },
          `${lineItems.length} line items · ${attached.length} documents recognised`))));

  form.dataset.lineItems = JSON.stringify(lineItems);
  form.dataset.attached = JSON.stringify(attached);
  form.dataset.policyId = policy?.policy_no || "not stated";
  form.dataset.icuCap = policy?.icu_cap_per_day ?? "";
  form.addEventListener("input", checkBlockers);
  queueMicrotask(checkBlockers);
  return form;
}

/** Every one of these blocks the audit rather than falling back to a default.
 *  A missing field that quietly becomes a plausible number is the defect this
 *  screen exists to avoid. */
function checkBlockers() {
  const form = document.getElementById("packet-form");
  if (!form) return [];
  const d = Object.fromEntries(new FormData(form));
  const items = JSON.parse(form.dataset.lineItems || "[]");
  const problems = [];

  if (!items.length) problems.push("No itemised bill among these documents — add the hospital bill.");
  if (num(d.room_rate) <= 0 || num(d.room_days) <= 0)
    problems.push("Enter the room rate and number of room days. The room-rent deduction cannot be estimated without them.");
  if (!String(d.diagnosis || "").trim())
    problems.push("Enter the primary diagnosis — it drives the document checklist.");
  if (d.admission && d.discharge && d.discharge < d.admission)
    problems.push("The discharge date is before the admission date.");

  const host = clear(document.getElementById("blockers"));
  for (const p of problems) host.append(banner("warn", p));
  const btn = document.getElementById("run-btn");
  if (btn) btn.disabled = problems.length > 0;
  return problems;
}

function buildPacket() {
  const form = document.getElementById("packet-form");
  const d = Object.fromEntries(new FormData(form));
  const items = JSON.parse(form.dataset.lineItems).map((it, i) => ({ ...it, line_no: i + 1 }));
  const name = (state.uploads[0]?.filename || "upload").replace(/\.[^.]+$/, "");

  return {
    // The engine validates claim_id; keep it to safe characters here so the failure
    // is a clear form error rather than a 422 from the boundary.
    claim_id: `UPLOAD-${name}`.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 40),
    context: {
      claim_type: d.claim_type,
      admission_date: d.admission,
      discharge_date: d.discharge,
      primary_diagnosis: String(d.diagnosis).trim(),
      procedure_performed: String(d.procedure || "").trim() || null,
      involves_implant: d.implant === "on",
      preauth_approved_amount: num(d.preauth) ? String(num(d.preauth)) : null,
      documents_attached: JSON.parse(form.dataset.attached),
    },
    policy: {
      // Absent terms stay absent. The waterfall reads null as "no limit"; an unstated
      // sub-limit must not become an invented one.
      policy_id: form.dataset.policyId,
      sum_insured: String(num(d.sum_insured)),
      balance_sum_insured: String(num(d.sum_insured)),
      room_rent_cap_per_day: num(d.room_cap) ? String(num(d.room_cap)) : null,
      icu_cap_per_day: form.dataset.icuCap || null,
      copay_percent: String(num(d.copay)),
      deductible: "0",
      procedure_sublimits: {},
    },
    room_stay: {
      room_category: String(d.room_category || "").trim() || "Room",
      rate_per_day: String(num(d.room_rate)),
      days: Math.round(num(d.room_days)),
      is_icu: d.is_icu === "on",
    },
    line_items: items,
  };
}

/* ---------------------------------------------------------------- audit -- */

function progress(steps, activeIndex) {
  return el("div", { class: "card" }, el("div", { class: "steps" },
    steps.map((label, i) => el("div", {
      class: `step ${i < activeIndex ? "done" : i === activeIndex ? "active" : ""}`,
    }, el("span", { class: "dot" }), label))));
}

async function runAudit() {
  if (checkBlockers().length) return;
  await execute(buildPacket());
}

async function auditSample(name) {
  try {
    const packet = await api.get(`/api/samples/${name}`);
    state.uploads = [];
    renderInput();
    await execute(packet);
  } catch (err) {
    toast(`The worked example could not be loaded: ${err.message}`, "bad", 8000);
  }
}

async function execute(packet) {
  const host = clear(document.getElementById("audit-result"));
  const steps = ["Classifying charges against the rule catalog",
                 "Computing deductions", "Verifying the numbers"];
  host.append(progress(steps, 0));

  try {
    state.packet = packet;
    state.result = await api.post("/api/audit", packet);
    renderResult();
    document.getElementById("audit-result")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    // Guarded. An unguarded failure here used to leave the progress indicator running
    // forever with a traceback underneath it.
    clear(host).append(
      banner("error", `The audit could not be completed: ${err.message}`),
      el("p", { class: "small muted" },
        "Your documents are still loaded — correct the details above and try again."));
  }
}

/* --------------------------------------------------------------- result -- */

function renderResult() {
  const host = clear(document.getElementById("audit-result"));
  if (!state.result) return;

  const r = state.result;
  const wf = r.profiles[state.profile] || r.profiles.typical;
  const v = VERDICT[r.verdict] || VERDICT.NEEDS_ATTENTION;
  const findings = collectFindings(r);
  const counts = findings.reduce((a, f) => ({ ...a, [f.severity]: (a[f.severity] || 0) + 1 }), {});

  let detail;
  if (r.verdict === "CLEAN") {
    detail = "Every line matched a rule, all required documents are attached, and the consistency checks passed.";
  } else if (r.verdict === "CANNOT_VERIFY") {
    detail = (r.caveats || []).join(" ") || "Internal checks disagreed with the figures below.";
  } else {
    const parts = ["BLOCKER", "WARNING", "INFO"]
      .filter((s) => counts[s]).map((s) => `${counts[s]} ${s.toLowerCase()}`);
    const action = counts.BLOCKER ? "Resolve the blockers before submitting"
      : counts.WARNING ? "None of these block submission, but each one is money"
      : "Nothing here blocks submission";
    detail = `${parts.join(", ")}. ${action} — every finding cites the rule it came from.`;
  }

  const gross = num(r.gross_bill);
  const pct = (x) => (gross > 0 ? (num(x) / gross) * 100 : 0);

  host.append(
    el("div", { class: `verdict ${v.css}` },
      el("div", { class: "mark", "aria-hidden": "true" }, v.mark),
      el("div", {}, el("h2", {}, v.title), el("p", {}, detail))),

    ...(r.verify_problems || []).map((p) => banner("error", `Internal check failed: ${p}`)),
    ...(r.errors || []).map((e) => banner("warn", e)),

    el("div", { class: "figs" },
      figure("Hospital absorbs", wf.hospital_writeoff, "fig-loss",
        "Already covered by room or procedure charges — repeats on every claim"),
      figure("Likely settlement", wf.projected_settlement, "",
        `Range ${rupees(Math.min(...Object.values(r.profiles).map((p) => num(p.projected_settlement))))} – ${rupees(Math.max(...Object.values(r.profiles).map((p) => num(p.projected_settlement))))}`),
      figure("Patient pays", wf.patient_liability, "", "Optional items plus policy deductions"),
      figure("Gross bill", r.gross_bill, "", `${Number(r.coverage ?? 1) * 100 | 0}% assessed against a rule`)),

    el("div", { class: "card" },
      el("div", { class: "split", role: "img",
        "aria-label": `Settlement ${pct(wf.projected_settlement).toFixed(0)}%, patient ${pct(wf.patient_liability).toFixed(0)}%, hospital ${pct(wf.hospital_writeoff).toFixed(0)}%` },
        el("span", { class: "s-settled", style: `width:${pct(wf.projected_settlement)}%` }),
        el("span", { class: "s-patient", style: `width:${pct(wf.patient_liability)}%` }),
        el("span", { class: "s-hospital", style: `width:${pct(wf.hospital_writeoff)}%` })),
      el("div", { class: "split-key small muted" },
        key("s-settled", "var(--settled)", "Insurer settles"),
        key("s-patient", "var(--patient)", "Patient pays"),
        key("s-hospital", "var(--hospital)", "Hospital absorbs"))),

    repeatingLine(r, wf),
    deductionCard(wf),
    disclosureCard(r),
    findingsCard(findings),
    lineItemsCard(r),
    actionsCard(r)
  );
}

const key = (cls, colour, label) =>
  el("span", {}, el("i", { style: `background:${colour}` }), label);

function figure(label, value, extra, sub) {
  return el("div", { class: `fig ${extra}` },
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, rupees(value)),
    sub ? el("div", { class: "sub" }, sub) : null);
}

function repeatingLine(r, wf) {
  const names = [...new Set((r.findings || [])
    .filter((f) => f.bearer === "HOSPITAL" && num(f.deducted_amount) > 0)
    .map((f) => f.description.trim()))];
  if (!names.length) return null;
  const shown = names.slice(0, 4).join(", ");
  const more = names.length > 4 ? ` and ${names.length - 4} more` : "";
  return el("div", { class: "card" },
    el("p", {},
      el("b", {}, `${rupees(wf.hospital_writeoff)} of this bill is a billing-template error, not a patient cost. `),
      `${names.length} item(s) — ${shown}${more} — are already covered by charges the hospital raised elsewhere. `,
      "Every claim carrying these lines loses the same money, until the master is corrected."));
}

function deductionCard(wf) {
  if (!wf.policy_deductions?.length) return null;
  const body = el("div", { class: "grid", style: "gap:18px" });
  for (const d of wf.policy_deductions) {
    body.append(el("div", {},
      el("div", { class: "row" },
        el("b", {}, d.step.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase())),
        el("span", { class: "spacer" }),
        el("b", {}, rupees(d.amount))),
      el("p", { class: "small muted", style: "margin:4px 0 8px" }, d.basis),
      Object.entries(d.inputs || {}).length
        ? el("dl", { class: "kv" }, Object.entries(d.inputs).flatMap(([k, val]) =>
            [el("dt", {}, k), el("dd", { class: "mono xs" }, val)]))
        : null,
      d.formula ? el("div", { class: "math", style: "margin-top:8px" }, d.formula) : null));
  }
  return el("details", { class: "card" },
    el("summary", { style: "cursor:pointer;font-weight:700" }, "How each policy deduction was calculated"),
    el("div", { style: "margin-top:16px" }, body,
      el("p", { class: "xs muted", style: "margin-top:12px" },
        "Amounts are exact to the paisa. Figures on screen are rounded to the nearest rupee, halves up.")));
}

function disclosureCard(r) {
  if (!r.disclosures?.length) return null;
  return el("details", { class: "card" },
    el("summary", { style: "cursor:pointer;font-weight:700" },
      `How this was checked, and what it does not cover (${r.disclosures.length})`),
    el("ul", { class: "small muted", style: "margin:12px 0 0;padding-left:18px" },
      r.disclosures.map((d) => el("li", { style: "margin-bottom:6px" }, d))));
}

function findingsCard(findings) {
  const card = el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", {}, `Findings (${findings.length})`),
      findings.length ? el("span", { class: "small muted" }, "Most urgent first") : null));

  if (!findings.length) {
    card.append(emptyState("No findings",
      "Every line matched a rule or a primary billing head, all required documents are attached, and the consistency checks passed."));
    return card;
  }

  const list = el("div", { class: "findings" });
  for (const f of findings) list.append(findingRow(f));
  card.append(list);
  return card;
}

function findingRow(f) {
  const impact = num(f.impact);
  const body = el("div", { class: "body" });

  const details = el("details", { class: "finding", ontoggle: () => {
    if (details.open && !details.dataset.loaded) { details.dataset.loaded = "1"; fillBody(); }
  } },
    el("summary", {},
      el("span", { class: `tag ${SEVERITY_TAG[f.severity] || "tag-mute"}` }, f.severity),
      el("span", { class: "title" }, f.title),
      impact ? el("span", { class: "amount" }, rupees(impact)) : null),
    body);

  function fillBody() {
    body.append(el("p", { class: "small" }, f.detail));

    if (f.field) {
      body.append(el("dl", { class: "kv" },
        el("dt", {}, "Found"), el("dd", {}, f.observed || "—"),
        el("dt", {}, "Expected"), el("dd", {}, f.expected || "—"),
        el("dt", {}, "Field"), el("dd", { class: "mono xs" }, f.field)));
    }

    if (f.citation) {
      body.append(el("div", { class: "cite" }, `${f.rule_id} — ${f.citation}`));
      if (/^(L|POL)/.test(f.rule_id || "")) {
        const quote = el("div", { class: "rule-quote small" }, "Loading rule text…");
        body.append(quote);
        rule(f.rule_id)
          .then((c) => clear(quote).append(el("b", {}, c.title), ` — ${c.body}`))
          .catch((err) => clear(quote).append(`Rule text unavailable: ${err.message}`));
      }
    }

    const text = [
      `[${f.severity}] ${f.title}`,
      f.detail,
      impact ? `Amount: ${rupees(impact)}` : null,
      f.citation ? `Rule: ${f.rule_id} — ${f.citation}` : null,
    ].filter(Boolean).join("\n");

    body.append(el("div", { class: "row" },
      el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => copy(text) },
        "Copy for your billing system")));
  }

  return details;
}

function lineItemsCard(r) {
  const only = el("input", { type: "checkbox", id: "only-flagged", style: "width:auto",
    onchange: () => draw() });
  const wrap = el("div", { class: "table-wrap" });

  function draw() {
    const rows = (r.findings || []).filter((f) =>
      !only.checked || num(f.deducted_amount) > 0 || f.classification === "UNMAPPED");
    clear(wrap).append(el("table", {},
      el("thead", {}, el("tr", {},
        ["#", "Item", "Category", "Amount", "Verdict", "Falls on", "Deducted", "Rule"]
          .map((h, i) => el("th", { class: i === 3 || i === 6 ? "num" : "" }, h)))),
      el("tbody", {}, rows.map((f) => el("tr", {},
        el("td", { class: "muted" }, f.line_no),
        el("td", {}, f.description),
        el("td", { class: "muted" }, f.head),
        el("td", { class: "num" }, rupees(f.amount)),
        el("td", {}, f.classification.replace("LIST_", "List ").replace(/_/g, " ")),
        el("td", { class: "muted" }, f.bearer),
        el("td", { class: "num" }, num(f.deducted_amount) ? rupees(f.deducted_amount) : "—"),
        el("td", { class: "mono xs muted" }, f.cited_chunk_id || "—"))))));
  }
  draw();

  return el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("h2", {}, "All line items"),
      el("label", { class: "row small muted", for: "only-flagged" },
        only, "Only deducted and unmatched")),
    wrap);
}

function actionsCard(r) {
  const card = el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "What to do")));

  if (r.narrative) card.append(el("p", { class: "small", style: "margin-bottom:12px" }, r.narrative));
  if (r.action_list?.length) {
    card.append(el("ol", { class: "small", style: "padding-left:18px" },
      r.action_list.map((a) => el("li", { style: "margin-bottom:6px" }, a))));
  }

  const btn = el("button", { class: "btn", onclick: async () => {
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Building…";
    try {
      const blob = await api.pdf("/api/report", state.packet, { profile: state.profile });
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: `${r.claim_id}-audit.pdf` });
      document.body.append(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast("Report downloaded");
    } catch (err) {
      toast(`Report failed: ${err.message}`, "bad", 8000);
    } finally { btn.disabled = false; btn.textContent = original; }
  } }, "Download audit report (PDF)");

  card.append(el("div", { class: "row", style: "margin-top:16px" },
    btn,
    el("button", { class: "btn btn-ghost", onclick: () => window.print() }, "Print"),
    el("span", { class: "xs muted" }, `Corpus ${r.corpus_version}`)));
  return card;
}

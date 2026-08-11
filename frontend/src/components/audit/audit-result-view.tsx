"use client";

import { useMemo, useState } from "react";
import { claimiqApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { rupees, num } from "@/lib/utils";
import {
  AlertTriangle,
  CheckCircle,
  XCircle,
  Download,
  ChevronDown,
  ChevronRight,
  Copy,
} from "lucide-react";
import type { AuditResult, ClaimPacket, ItemFinding } from "@/lib/types";

interface AuditResultViewProps {
  result: AuditResult;
  packet: ClaimPacket;
  profile: string;
}

const VERDICT_CONFIG = {
  CLEAN: {
    icon: CheckCircle,
    title: "Clean",
    css: "border-settled/30 bg-settled/5",
    iconCss: "text-settled",
  },
  NEEDS_ATTENTION: {
    icon: AlertTriangle,
    title: "Needs attention",
    css: "border-amber-500/30 bg-amber-500/5",
    iconCss: "text-amber-400",
  },
  CANNOT_VERIFY: {
    icon: XCircle,
    title: "Cannot verify",
    css: "border-red-500/30 bg-red-500/5",
    iconCss: "text-red-400",
  },
};

export function AuditResultView({ result, packet, profile }: AuditResultViewProps) {
  const { toast } = useToast();
  const wf = result.profiles[profile] || result.profiles.typical;
  const v = VERDICT_CONFIG[result.verdict] || VERDICT_CONFIG.NEEDS_ATTENTION;
  const VerdictIcon = v.icon;

  const gross = num(result.gross_bill);
  const pct = (x: string) => (gross > 0 ? (num(x) / gross) * 100 : 0);

  const nonPayableFindings = useMemo(
    () => result.findings.filter((f) => f.classification !== "PAYABLE"),
    [result.findings]
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    nonPayableFindings.forEach((f) => {
      c[f.severity] = (c[f.severity] || 0) + 1;
    });
    return c;
  }, [nonPayableFindings]);

  const summaryText = useMemo(() => {
    if (result.verdict === "CLEAN")
      return "Every line matched a rule, all required documents are attached, and the consistency checks passed.";
    if (result.verdict === "CANNOT_VERIFY")
      return result.caveats.join(" ") || "Internal checks disagreed with the figures below.";
    const parts = ["BLOCKER", "WARNING", "INFO"]
      .filter((s) => counts[s])
      .map((s) => `${counts[s]} ${s.toLowerCase()}`);
    return `${parts.join(", ")}. Every finding cites the rule it came from.`;
  }, [result, counts]);

  const handleDownloadPdf = async () => {
    try {
      const blob = await claimiqApi.report(packet, profile);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${result.claim_id}-audit.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("Report downloaded", "success");
    } catch (err) {
      toast(`Report failed: ${(err as Error).message}`, "error");
    }
  };

  return (
    <div className="space-y-4">
      {/* Verdict */}
      <div className={`card border ${v.css} flex items-start gap-4`}>
        <VerdictIcon size={24} className={v.iconCss} />
        <div>
          <h2 className="font-bold text-lg">{v.title}</h2>
          <p className="text-sm text-muted mt-1">{summaryText}</p>
        </div>
      </div>

      {/* Errors / verify problems */}
      {result.verify_problems.map((p, i) => (
        <div key={i} className="p-3 bg-red-500/5 border border-red-500/20 rounded text-sm text-red-400">
          Internal check failed: {p}
        </div>
      ))}
      {result.errors.map((e, i) => (
        <div key={i} className="p-3 bg-amber-500/5 border border-amber-500/20 rounded text-sm text-amber-400">
          {e}
        </div>
      ))}

      {/* Figures */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <FigureCard
          label="Hospital absorbs"
          value={rupees(wf.hospital_writeoff)}
          className="border-hospital/30"
          valueClass="text-hospital"
          sub="Repeats on every claim"
        />
        <FigureCard
          label="Likely settlement"
          value={rupees(wf.projected_settlement)}
          className="border-settled/30"
          valueClass="text-settled"
          sub={`Range ${rupees(
            Math.min(...Object.values(result.profiles).map((p) => num(p.projected_settlement)))
          )} – ${rupees(
            Math.max(...Object.values(result.profiles).map((p) => num(p.projected_settlement)))
          )}`}
        />
        <FigureCard
          label="Patient pays"
          value={rupees(wf.patient_liability)}
          className="border-patient/30"
          valueClass="text-patient"
          sub="Optional items + policy deductions"
        />
        <FigureCard
          label="Gross bill"
          value={rupees(result.gross_bill)}
          className=""
          valueClass="text-white"
          sub={`${(num(result.coverage) * 100).toFixed(0)}% assessed`}
        />
      </div>

      {/* Split bar */}
      <div className="card">
        <div className="flex h-3 rounded-full overflow-hidden">
          <div
            className="bg-settled"
            style={{ width: `${pct(wf.projected_settlement)}%` }}
          />
          <div
            className="bg-patient"
            style={{ width: `${pct(wf.patient_liability)}%` }}
          />
          <div
            className="bg-hospital"
            style={{ width: `${pct(wf.hospital_writeoff)}%` }}
          />
        </div>
        <div className="flex items-center gap-4 mt-3 text-xs text-muted">
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm bg-settled" /> Insurer settles
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm bg-patient" /> Patient pays
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm bg-hospital" /> Hospital absorbs
          </span>
        </div>
      </div>

      {/* Billing template error */}
      {num(wf.hospital_writeoff) > 0 && (
        <HospitalWriteoffCard result={result} writeoff={wf.hospital_writeoff} />
      )}

      {/* Deduction breakdown */}
      {wf.policy_deductions.length > 0 && (
        <DeductionBreakdown deductions={wf.policy_deductions} />
      )}

      {/* Findings */}
      <FindingsSection findings={nonPayableFindings} />

      {/* Line items table */}
      <LineItemsTable findings={result.findings} />

      {/* Actions */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold">What to do</h2>
        {result.narrative && (
          <p className="text-sm text-muted">{result.narrative}</p>
        )}
        {result.action_list.length > 0 && (
          <ol className="list-decimal list-inside space-y-1 text-sm text-muted">
            {result.action_list.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ol>
        )}
        <div className="flex items-center gap-3 pt-2">
          <Button variant="primary" onClick={handleDownloadPdf}>
            <Download size={14} /> Download audit report (PDF)
          </Button>
          <span className="text-xs text-muted">
            Corpus {result.corpus_version}
          </span>
        </div>
      </div>

      {/* Disclosures */}
      {result.disclosures.length > 0 && (
        <details className="card">
          <summary className="cursor-pointer font-semibold text-sm">
            How this was checked ({result.disclosures.length})
          </summary>
          <ul className="mt-3 space-y-1 text-xs text-muted list-disc list-inside">
            {result.disclosures.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// -- Sub-components --

function FigureCard({
  label,
  value,
  sub,
  className,
  valueClass,
}: {
  label: string;
  value: string;
  sub: string;
  className: string;
  valueClass: string;
}) {
  return (
    <div className={`card border ${className}`}>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-xl font-bold font-mono mt-1 ${valueClass}`}>
        {value}
      </div>
      <div className="text-xs text-muted-2 mt-1">{sub}</div>
    </div>
  );
}

function HospitalWriteoffCard({
  result,
  writeoff,
}: {
  result: AuditResult;
  writeoff: string;
}) {
  const names = [
    ...new Set(
      result.findings
        .filter((f) => f.bearer === "HOSPITAL" && num(f.deducted_amount) > 0)
        .map((f) => f.description.trim())
    ),
  ];
  if (!names.length) return null;

  const shown = names.slice(0, 4).join(", ");
  const more = names.length > 4 ? ` and ${names.length - 4} more` : "";

  return (
    <div className="card border border-hospital/20 bg-hospital/5">
      <p className="text-sm">
        <strong className="text-hospital">{rupees(writeoff)}</strong> of this
        bill is a billing-template error, not a patient cost.{" "}
        <span className="text-muted">
          {names.length} item(s) — {shown}
          {more} — are already covered by charges the hospital raised elsewhere.
          Every claim carrying these lines loses the same money, until the master
          is corrected.
        </span>
      </p>
    </div>
  );
}

function DeductionBreakdown({
  deductions,
}: {
  deductions: AuditResult["profiles"]["typical"]["policy_deductions"];
}) {
  return (
    <details className="card">
      <summary className="cursor-pointer font-semibold text-sm">
        How each policy deduction was calculated
      </summary>
      <div className="mt-4 space-y-4">
        {deductions.map((d, i) => (
          <div key={i} className="border-b border-line pb-4 last:border-0">
            <div className="flex justify-between items-center">
              <span className="font-medium text-sm capitalize">
                {d.step.replace(/_/g, " ")}
              </span>
              <span className="font-bold font-mono text-sm">
                {rupees(d.amount)}
              </span>
            </div>
            <p className="text-xs text-muted mt-1">{d.basis}</p>
            {Object.keys(d.inputs).length > 0 && (
              <dl className="grid grid-cols-2 gap-1 mt-2 text-xs">
                {Object.entries(d.inputs).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-2">{k}</dt>
                    <dd className="font-mono text-muted">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {d.formula && (
              <pre className="mt-2 p-2 bg-panel-3 rounded text-xs font-mono text-muted overflow-x-auto">
                {d.formula}
              </pre>
            )}
          </div>
        ))}
        <p className="text-xs text-muted-2">
          Amounts are exact to the paisa. Figures on screen are rounded to the
          nearest rupee, halves up.
        </p>
      </div>
    </details>
  );
}

function FindingsSection({ findings }: { findings: ItemFinding[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2 className="text-sm font-semibold">Findings ({findings.length})</h2>
        {findings.length > 0 && (
          <span className="text-xs text-muted">Most urgent first</span>
        )}
      </div>
      {findings.length === 0 ? (
        <p className="text-sm text-muted py-4 text-center">
          No findings. Every line matched a rule or a primary billing head.
        </p>
      ) : (
        <div className="space-y-1">
          {findings
            .sort(
              (a, b) =>
                severityOrder(a.severity) - severityOrder(b.severity) ||
                num(b.deducted_amount) - num(a.deducted_amount)
            )
            .map((f, i) => (
              <FindingRow key={i} finding={f} />
            ))}
        </div>
      )}
    </div>
  );
}

function severityOrder(s: string): number {
  return s === "BLOCKER" ? 0 : s === "WARNING" ? 1 : 2;
}

function FindingRow({ finding }: { finding: ItemFinding }) {
  const [open, setOpen] = useState(false);
  const [ruleText, setRuleText] = useState<string | null>(null);
  const { toast } = useToast();

  const severityVariant = finding.severity === "BLOCKER" ? "blocker" : finding.severity === "WARNING" ? "warning" : "info";

  const handleToggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !ruleText && finding.cited_chunk_id) {
      try {
        const chunk = await claimiqApi.rule(finding.cited_chunk_id);
        setRuleText(`${chunk.title} — ${chunk.body}`);
      } catch {
        setRuleText("Rule text unavailable");
      }
    }
  };

  const handleCopy = () => {
    const text = [
      `[${finding.severity}] ${finding.description}`,
      finding.reason,
      num(finding.deducted_amount) ? `Amount: ${rupees(finding.deducted_amount)}` : null,
      finding.citation ? `Rule: ${finding.rule_id} — ${finding.citation}` : null,
    ]
      .filter(Boolean)
      .join("\n");
    navigator.clipboard.writeText(text);
    toast("Copied to clipboard", "success");
  };

  return (
    <div className="border border-line rounded-md overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-panel-3 transition-colors text-left"
      >
        {open ? <ChevronDown size={14} className="text-muted shrink-0" /> : <ChevronRight size={14} className="text-muted shrink-0" />}
        <Badge variant={severityVariant}>{finding.severity}</Badge>
        <span className="text-sm flex-1 truncate">{finding.description}</span>
        {num(finding.deducted_amount) > 0 && (
          <span className="font-mono text-sm font-bold text-hospital">
            {rupees(finding.deducted_amount)}
          </span>
        )}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-line pt-3">
          <p className="text-sm text-muted">{finding.reason}</p>
          {finding.field && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-2">Found</dt>
              <dd>{finding.observed || "—"}</dd>
              <dt className="text-muted-2">Expected</dt>
              <dd>{finding.expected || "—"}</dd>
              <dt className="text-muted-2">Field</dt>
              <dd className="font-mono">{finding.field}</dd>
            </dl>
          )}
          {finding.citation && (
            <div className="text-xs p-2 bg-panel-3 rounded text-accent">
              {finding.rule_id} — {finding.citation}
            </div>
          )}
          {ruleText && (
            <div className="text-xs text-muted p-2 bg-panel-3 rounded">
              {ruleText}
            </div>
          )}
          <Button size="sm" variant="ghost" onClick={handleCopy}>
            <Copy size={12} /> Copy for billing system
          </Button>
        </div>
      )}
    </div>
  );
}

function LineItemsTable({ findings }: { findings: ItemFinding[] }) {
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  const filtered = useMemo(
    () =>
      onlyFlagged
        ? findings.filter(
            (f) => num(f.deducted_amount) > 0 || f.classification === "UNMAPPED"
          )
        : findings,
    [findings, onlyFlagged]
  );

  return (
    <div className="card">
      <div className="card-head">
        <h2 className="text-sm font-semibold">All line items</h2>
        <label className="flex items-center gap-2 text-xs text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={onlyFlagged}
            onChange={(e) => setOnlyFlagged(e.target.checked)}
            className="w-3.5 h-3.5 accent-accent"
          />
          Only deducted and unmatched
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line text-muted-2 text-left">
              <th className="py-2 px-2">#</th>
              <th className="py-2 px-2">Item</th>
              <th className="py-2 px-2">Category</th>
              <th className="py-2 px-2 text-right">Amount</th>
              <th className="py-2 px-2">Verdict</th>
              <th className="py-2 px-2">Falls on</th>
              <th className="py-2 px-2 text-right">Deducted</th>
              <th className="py-2 px-2">Rule</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((f, i) => (
              <tr
                key={i}
                className="border-b border-line/50 hover:bg-panel-3"
              >
                <td className="py-2 px-2 text-muted-2">{f.line_no}</td>
                <td className="py-2 px-2 max-w-[200px] truncate">{f.description}</td>
                <td className="py-2 px-2 text-muted">{f.head}</td>
                <td className="py-2 px-2 text-right font-mono">{rupees(f.amount)}</td>
                <td className="py-2 px-2">
                  <span className="text-xs">
                    {f.classification.replace("LIST_", "List ").replace(/_/g, " ")}
                  </span>
                </td>
                <td className="py-2 px-2 text-muted">{f.bearer}</td>
                <td className="py-2 px-2 text-right font-mono">
                  {num(f.deducted_amount) ? rupees(f.deducted_amount) : "—"}
                </td>
                <td className="py-2 px-2 font-mono text-muted-2 truncate max-w-[100px]">
                  {f.cited_chunk_id || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

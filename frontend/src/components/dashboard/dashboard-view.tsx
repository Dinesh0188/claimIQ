"use client";

import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { rupees, num } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

export function DashboardView() {
  const { data: summary, isLoading: loadingSummary } = useQuery({
    queryKey: ["analytics-summary"],
    queryFn: claimiqApi.analyticsSummary,
  });

  const { data: leakage } = useQuery({
    queryKey: ["analytics-leakage"],
    queryFn: claimiqApi.analyticsLeakage,
  });

  const { data: topItems } = useQuery({
    queryKey: ["analytics-top-items"],
    queryFn: () => claimiqApi.analyticsTopItems(10),
  });

  const { data: missingDocs } = useQuery({
    queryKey: ["analytics-missing-docs"],
    queryFn: () => claimiqApi.analyticsMissingDocs(10),
  });

  if (loadingSummary) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (!summary || summary.empty) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold mb-2">Leakage Dashboard</h1>
        <div className="card text-center py-12">
          <p className="text-muted">No claims audited yet.</p>
          <p className="text-xs text-muted-2 mt-1">
            Audit some claims from the Check a claim page to see portfolio analytics here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Leakage Dashboard</h1>
        <p className="text-muted mt-1">
          Portfolio-level view of where money is being lost across{" "}
          {summary.claims} audited claims.
        </p>
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard label="Total claims" value={String(summary.claims)} />
        <StatCard label="Gross billed" value={rupees(summary.gross)} />
        <StatCard
          label="Total settlement"
          value={rupees(summary.settlement)}
          valueClass="text-settled"
        />
        <StatCard
          label="Hospital write-off"
          value={rupees(summary.hospital_writeoff)}
          valueClass="text-hospital"
        />
        <StatCard
          label="Avg deduction"
          value={`${num(summary.avg_deduction_pct).toFixed(1)}%`}
          valueClass="text-amber-400"
        />
      </div>

      {/* Preventable loss */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <StatCard
          label="Preventable (hospital)"
          value={rupees(summary.preventable)}
          valueClass="text-hospital"
          sub="Billing errors that repeat every claim"
        />
        <StatCard
          label="Room rent deductions"
          value={rupees(summary.room_rent_deduction)}
          valueClass="text-patient"
          sub="Cap + proportionate deduction"
        />
        <StatCard
          label="Document gaps"
          value={String(summary.doc_gaps)}
          valueClass="text-amber-400"
          sub="Missing documents across all claims"
        />
      </div>

      {/* Leakage chart */}
      {leakage && leakage.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-4">Leakage by cause</h2>
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={leakage}
                layout="vertical"
                margin={{ left: 180, right: 30, top: 10, bottom: 10 }}
              >
                <XAxis
                  type="number"
                  tick={{ fill: "#9aa1ac", fontSize: 11 }}
                  tickFormatter={(v) => `₹${Math.round(v / 1000)}k`}
                />
                <YAxis
                  type="category"
                  dataKey="cause"
                  tick={{ fill: "#9aa1ac", fontSize: 11 }}
                  width={170}
                />
                <Tooltip
                  contentStyle={{
                    background: "#14161b",
                    border: "1px solid #23262e",
                    borderRadius: "8px",
                    fontSize: "12px",
                  }}
                  formatter={(value: number) => [rupees(value), "Amount"]}
                />
                <Bar dataKey="amount" radius={[0, 4, 4, 0]}>
                  {leakage.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.bearer === "HOSPITAL" ? "#ff5c5c" : "#5aa9ff"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex items-center gap-4 mt-3 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-hospital" /> Hospital absorbs
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-patient" /> Patient pays
            </span>
          </div>
        </div>
      )}

      {/* Top leaking items */}
      {topItems && topItems.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-4">
            Top billing errors (hospital loss)
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2 px-3">Item</th>
                  <th className="py-2 px-3 text-right">Claims</th>
                  <th className="py-2 px-3 text-right">Total deducted</th>
                </tr>
              </thead>
              <tbody>
                {topItems.map((item, i) => (
                  <tr
                    key={i}
                    className="border-b border-line/50 hover:bg-panel-3"
                  >
                    <td className="py-2.5 px-3 capitalize">{item.item}</td>
                    <td className="py-2.5 px-3 text-right text-muted">
                      {item.claims}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-hospital">
                      {rupees(item.total_deducted)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Missing documents */}
      {missingDocs && missingDocs.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-4">
            Most-missed documents
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2 px-3">Document</th>
                  <th className="py-2 px-3">Severity</th>
                  <th className="py-2 px-3 text-right">Claims affected</th>
                </tr>
              </thead>
              <tbody>
                {missingDocs.map((doc, i) => (
                  <tr
                    key={i}
                    className="border-b border-line/50 hover:bg-panel-3"
                  >
                    <td className="py-2.5 px-3">{doc.name}</td>
                    <td className="py-2.5 px-3">
                      <Badge
                        variant={
                          doc.severity === "BLOCKER"
                            ? "blocker"
                            : doc.severity === "WARNING"
                            ? "warning"
                            : "info"
                        }
                      >
                        {doc.severity}
                      </Badge>
                    </td>
                    <td className="py-2.5 px-3 text-right text-muted">
                      {doc.claims}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  valueClass = "text-white",
  sub,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: string;
}) {
  return (
    <div className="card">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-bold font-mono mt-1 ${valueClass}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-muted-2 mt-1">{sub}</div>}
    </div>
  );
}

"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { rupees, num, formatDate } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { LayoutDashboard } from "lucide-react";

const DashboardChart = dynamic(
  () => import("./dashboard-chart").then((m) => m.DashboardChart),
  {
    ssr: false,
    loading: () => (
      <div className="h-[300px] flex items-center justify-center">
        <Spinner />
      </div>
    ),
  }
);

export function DashboardView() {
  const {
    data: summary,
    isLoading: loadingSummary,
    isError: errorSummary,
    refetch: refetchSummary,
  } = useQuery({
    queryKey: ["analytics-summary"],
    queryFn: claimiqApi.analyticsSummary,
  });

  const {
    data: leakage,
    isLoading: loadingLeakage,
    isError: errorLeakage,
    refetch: refetchLeakage,
  } = useQuery({
    queryKey: ["analytics-leakage"],
    queryFn: claimiqApi.analyticsLeakage,
  });

  const {
    data: topItems,
    isLoading: loadingTop,
    isError: errorTop,
    refetch: refetchTop,
  } = useQuery({
    queryKey: ["analytics-top-items"],
    queryFn: () => claimiqApi.analyticsTopItems(10),
  });

  const {
    data: missingDocs,
    isLoading: loadingDocs,
    isError: errorDocs,
    refetch: refetchDocs,
  } = useQuery({
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

  if (errorSummary) {
    return (
      <div className="max-w-5xl mx-auto space-y-4">
        <h1 className="text-2xl font-bold">Leakage Dashboard</h1>
        <div className="card text-center py-8">
          <p className="text-red-400">Failed to load summary. Please retry.</p>
          <button
            onClick={() => refetchSummary()}
            className="mt-4 px-4 py-2 bg-accent text-white rounded-md text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  if (!summary || summary.empty) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold mb-2">Leakage Dashboard</h1>
        <div className="card text-center py-12">
          <div className="flex justify-center mb-3">
            <div className="w-12 h-12 rounded-full bg-panel-3 flex items-center justify-center">
              <LayoutDashboard size={20} className="text-muted" aria-hidden="true" />
            </div>
          </div>
          <p className="text-muted">No audited claims yet.</p>
          <p className="text-xs text-muted-2 mt-1">
            Audit some claims from the Check a claim page to see portfolio analytics here.
          </p>
          <Link
            href="/"
            className="inline-flex mt-5 px-4 py-2 bg-accent text-white rounded-md text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Check a Claim
          </Link>
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

      {/* Leakage chart - independent section with loading/error */}
      <div className="card">
        <h2 className="text-sm font-semibold mb-4">Leakage by Cause</h2>
        {loadingLeakage ? (
          <div className="h-[300px] flex items-center justify-center">
            <Spinner />
          </div>
        ) : errorLeakage ? (
          <div className="text-center py-8">
            <p className="text-sm text-red-400">Failed to load leakage chart.</p>
            <button
              onClick={() => refetchLeakage()}
              className="mt-3 px-3 py-1.5 bg-panel-3 border border-line rounded text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Retry
            </button>
          </div>
        ) : leakage && leakage.length > 0 ? (
          <DashboardChart data={leakage} />
        ) : (
          <p className="text-sm text-muted-2">No leakage data yet.</p>
        )}
      </div>

      {/* Top leaking items - section with independent state */}
      <div className="card">
        <h2 className="text-sm font-semibold mb-4">Top Billing Errors (Hospital Loss)</h2>
        {loadingTop ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : errorTop ? (
          <div className="text-center py-6">
            <p className="text-sm text-red-400">Failed to load top items.</p>
            <button
              onClick={() => refetchTop()}
              className="mt-3 px-3 py-1.5 bg-panel-3 border border-line rounded text-xs"
            >
              Retry
            </button>
          </div>
        ) : topItems && topItems.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2 px-3 min-w-[160px]">Item</th>
                  <th className="py-2 px-3 text-right tabular-nums">Claims</th>
                  <th className="py-2 px-3 text-right tabular-nums">Total Deducted</th>
                </tr>
              </thead>
              <tbody>
                {topItems.map((item, i) => (
                  <tr key={i} className="border-b border-line/50 hover:bg-panel-3">
                    <td className="py-2.5 px-3 capitalize min-w-0 max-w-[240px] truncate">
                      {item.item}
                    </td>
                    <td className="py-2.5 px-3 text-right text-muted tabular-nums">
                      {item.claims}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-hospital tabular-nums">
                      {rupees(item.total_deducted)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-2">No billing errors yet.</p>
        )}
      </div>

      {/* Missing documents - independent */}
      <div className="card">
        <h2 className="text-sm font-semibold mb-4">Most-Missed Documents</h2>
        {loadingDocs ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : errorDocs ? (
          <div className="text-center py-6">
            <p className="text-sm text-red-400">Failed to load missing documents.</p>
            <button
              onClick={() => refetchDocs()}
              className="mt-3 px-3 py-1.5 bg-panel-3 border border-line rounded text-xs"
            >
              Retry
            </button>
          </div>
        ) : missingDocs && missingDocs.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2 px-3 min-w-[160px]">Document</th>
                  <th className="py-2 px-3">Severity</th>
                  <th className="py-2 px-3 text-right tabular-nums">Claims Affected</th>
                </tr>
              </thead>
              <tbody>
                {missingDocs.map((doc, i) => (
                  <tr key={i} className="border-b border-line/50 hover:bg-panel-3">
                    <td className="py-2.5 px-3 min-w-0 max-w-[240px] truncate">{doc.name}</td>
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
                    <td className="py-2.5 px-3 text-right text-muted tabular-nums">
                      {doc.claims}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-2">No missing document data yet.</p>
        )}
      </div>
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

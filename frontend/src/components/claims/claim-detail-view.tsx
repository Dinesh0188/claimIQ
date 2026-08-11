"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { rupees, num } from "@/lib/utils";
import { ArrowLeft } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface Props {
  claimId: string;
}

export function ClaimDetailView({ claimId }: Props) {
  const router = useRouter();
  const { data: claim, isLoading, error } = useQuery({
    queryKey: ["claim", claimId],
    queryFn: () => claimiqApi.claimDetail(claimId),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (error || !claim) {
    return (
      <div className="card text-center py-12">
        <p className="text-red-400">
          {(error as Error)?.message || "Claim not found."}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => router.push("/claims")}
        >
          Back to claims
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/claims")}
          className="p-2 hover:bg-panel-3 rounded"
        >
          <ArrowLeft size={18} className="text-muted" />
        </button>
        <div>
          <h1 className="text-xl font-bold font-mono">{claim.claim_id}</h1>
          <p className="text-sm text-muted">
            {claim.diagnosis}
            {claim.procedure ? ` · ${claim.procedure}` : ""} ·{" "}
            {claim.audited_at?.slice(0, 10)}
          </p>
        </div>
        {claim.ai_pipeline && <Badge variant="success">AI pipeline</Badge>}
      </div>

      {/* Figures */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card">
          <div className="text-xs text-muted">Gross bill</div>
          <div className="text-xl font-bold font-mono mt-1">
            {rupees(claim.gross_bill)}
          </div>
        </div>
        <div className="card border border-settled/30">
          <div className="text-xs text-muted">Settlement</div>
          <div className="text-xl font-bold font-mono text-settled mt-1">
            {rupees(claim.settlement)}
          </div>
        </div>
        <div className="card border border-patient/30">
          <div className="text-xs text-muted">Patient liability</div>
          <div className="text-xl font-bold font-mono text-patient mt-1">
            {rupees(claim.patient_liability)}
          </div>
        </div>
        <div className="card border border-hospital/30">
          <div className="text-xs text-muted">Hospital write-off</div>
          <div className="text-xl font-bold font-mono text-hospital mt-1">
            {rupees(claim.hospital_writeoff)}
          </div>
        </div>
      </div>

      {/* Findings table */}
      {claim.findings && claim.findings.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-4">
            Line item findings ({claim.findings.length})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left">
                  <th className="py-2 px-2">#</th>
                  <th className="py-2 px-2">Item</th>
                  <th className="py-2 px-2">Head</th>
                  <th className="py-2 px-2">Classification</th>
                  <th className="py-2 px-2">Bearer</th>
                  <th className="py-2 px-2 text-right">Amount</th>
                  <th className="py-2 px-2 text-right">Deducted</th>
                  <th className="py-2 px-2">Rule</th>
                </tr>
              </thead>
              <tbody>
                {claim.findings.map((f, i) => (
                  <tr
                    key={i}
                    className="border-b border-line/50 hover:bg-panel-3"
                  >
                    <td className="py-2 px-2 text-muted-2">{f.line_no}</td>
                    <td className="py-2 px-2 max-w-[200px] truncate">
                      {f.description}
                    </td>
                    <td className="py-2 px-2 text-muted">{f.head}</td>
                    <td className="py-2 px-2">
                      {f.classification.replace("LIST_", "L").replace(/_/g, " ")}
                    </td>
                    <td className="py-2 px-2 text-muted">{f.bearer}</td>
                    <td className="py-2 px-2 text-right font-mono">
                      {rupees(f.amount)}
                    </td>
                    <td className="py-2 px-2 text-right font-mono text-hospital">
                      {num(f.deducted_amount) ? rupees(f.deducted_amount) : "—"}
                    </td>
                    <td className="py-2 px-2 font-mono text-muted-2 truncate max-w-[80px]">
                      {f.cited_chunk_id || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Document gaps */}
      {claim.document_gaps && claim.document_gaps.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-3">
            Missing documents ({claim.document_gaps.length})
          </h2>
          <div className="space-y-2">
            {claim.document_gaps.map((g, i) => (
              <div
                key={i}
                className="flex items-center gap-3 p-2 bg-panel-3 rounded"
              >
                <Badge
                  variant={
                    g.severity === "BLOCKER"
                      ? "blocker"
                      : g.severity === "WARNING"
                      ? "warning"
                      : "info"
                  }
                >
                  {g.severity}
                </Badge>
                <span className="text-sm">{g.name}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

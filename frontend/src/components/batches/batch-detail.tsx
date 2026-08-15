"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { rupees, cn } from "@/lib/utils";
import { ArrowLeft, Ban } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { JobState, ClaimOutcome } from "@/lib/types";

interface Props {
  jobId: string;
}

function stateVariant(state: JobState) {
  switch (state) {
    case "queued":
      return "muted" as const;
    case "running":
      return "info" as const;
    case "done":
      return "success" as const;
    case "cancelled":
      return "muted" as const;
  }
}

function verdictVariant(verdict: string) {
  if (verdict === "CLEAN") return "success" as const;
  if (verdict === "NEEDS_ATTENTION") return "warning" as const;
  if (verdict === "CANNOT_VERIFY") return "info" as const;
  return "muted" as const;
}

export function BatchDetail({ jobId }: Props) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const { data: job, isLoading, error } = useQuery({
    queryKey: ["batch", jobId],
    queryFn: () => claimiqApi.batchStatus(jobId),
    refetchInterval: (query) =>
      query.state.data &&
      (query.state.data.state === "queued" ||
        query.state.data.state === "running")
        ? 2000
        : false,
  });

  const cancelMutation = useMutation({
    mutationFn: () => claimiqApi.cancelBatch(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batch", jobId] });
      queryClient.invalidateQueries({ queryKey: ["batches"] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (error || !job) {
    const notFound = (error as { status?: number } | null)?.status === 404;
    return (
      <div className="card text-center py-12">
        <p className="text-red-400">
          {notFound ? "Batch not found." : (error as Error)?.message}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => router.push("/batches")}
        >
          Back to batches
        </Button>
      </div>
    );
  }

  const active = job.state === "queued" || job.state === "running";
  const pct = Math.round(job.progress * 100);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/batches")}
          className="p-2 hover:bg-panel-3 rounded"
        >
          <ArrowLeft size={18} className="text-muted" />
        </button>
        <div>
          <h1 className="text-xl font-bold font-mono">{job.job_id}</h1>
          <p className="text-sm text-muted">
            Submitted {job.submitted_at?.slice(0, 10)}
          </p>
        </div>
        <Badge variant={stateVariant(job.state)} className="ml-2">
          {job.state}
        </Badge>
        {active && (
          <Button
            variant="danger"
            size="sm"
            className="ml-auto"
            disabled={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            <Ban size={14} /> Cancel
          </Button>
        )}
      </div>

      {/* Progress */}
      <div className="card space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Progress</span>
          <span className="font-mono">{pct}%</span>
        </div>
        <div className="h-2 bg-panel-3 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="flex items-center gap-6 text-xs text-muted">
          <span>{job.completed} completed</span>
          <span>{job.total - job.completed} remaining</span>
          {job.failed > 0 && (
            <span className="text-red-400">{job.failed} failed</span>
          )}
          {job.state === "running" && (
            <span className="text-accent animate-pulse">Auditing...</span>
          )}
          {job.state === "cancelled" && (
            <span className="text-muted">Stopped after the in-flight claim</span>
          )}
        </div>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <div className="card border border-settled/30">
          <div className="text-xs text-muted">Settlement</div>
          <div className="text-xl font-bold font-mono text-settled mt-1">
            {rupees(job.totals.settlement)}
          </div>
        </div>
        <div className="card border border-patient/30">
          <div className="text-xs text-muted">Patient liability</div>
          <div className="text-xl font-bold font-mono text-patient mt-1">
            {rupees(job.totals.patient_liability)}
          </div>
        </div>
        <div className="card border border-hospital/30">
          <div className="text-xs text-muted">Hospital write-off</div>
          <div className="text-xl font-bold font-mono text-hospital mt-1">
            {rupees(job.totals.hospital_writeoff)}
          </div>
        </div>
        <div className="card">
          <div className="text-xs text-muted">Needs attention</div>
          <div className="text-xl font-bold font-mono text-amber-400 mt-1">
            {job.totals.needs_attention}
          </div>
        </div>
        <div className="card">
          <div className="text-xs text-muted">Clean</div>
          <div className="text-xl font-bold font-mono text-settled mt-1">
            {job.totals.clean}
          </div>
        </div>
      </div>

      {/* Outcomes */}
      {job.outcomes && job.outcomes.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-4">
            Claim outcomes ({job.outcomes.length})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2.5 px-3">Claim</th>
                  <th className="py-2.5 px-3">Verdict</th>
                  <th className="py-2.5 px-3 text-right">Settlement</th>
                  <th className="py-2.5 px-3 text-right">Patient</th>
                  <th className="py-2.5 px-3 text-right">Write-off</th>
                  <th className="py-2.5 px-3">Error</th>
                </tr>
              </thead>
              <tbody>
                {job.outcomes.map((o: ClaimOutcome) => (
                  <tr
                    key={o.claim_id}
                    className={cn(
                      "border-b border-line/50 hover:bg-panel-3",
                      !o.ok && "bg-red-500/5"
                    )}
                  >
                    <td className="py-2.5 px-3 font-mono text-xs text-accent">
                      {o.claim_id}
                    </td>
                    <td className="py-2.5 px-3">
                      {o.ok ? (
                        <Badge variant={verdictVariant(o.verdict)}>
                          {o.verdict}
                        </Badge>
                      ) : (
                        <Badge variant="blocker">FAILED</Badge>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-settled">
                      {o.ok ? rupees(o.settlement) : "—"}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-patient">
                      {o.ok ? rupees(o.patient_liability) : "—"}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-hospital">
                      {o.ok ? rupees(o.hospital_writeoff) : "—"}
                    </td>
                    <td className="py-2.5 px-3 text-xs text-red-400 max-w-[280px] truncate">
                      {o.error || "—"}
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

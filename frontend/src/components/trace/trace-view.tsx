"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { claimiqApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";
import { Button } from "@/components/ui/button";

interface Props {
  claimId: string;
}

function isRepairNode(note: string, attempts: number): boolean {
  const lower = (note || "").toLowerCase();
  return attempts > 1 || /repair|verify/.test(lower);
}

export function TraceView({ claimId }: Props) {
  const { data: trace, isLoading, error } = useQuery({
    queryKey: ["trace", claimId],
    queryFn: () => claimiqApi.trace(claimId),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (error || !trace) {
    return (
      <div className="card text-center py-12 space-y-4">
        <p className="text-red-400">
          {(error as Error)?.message || "No trace for this claim."}
        </p>
        <p className="text-sm text-muted">
          No trace for this claim — run an audit to see the node trace.
        </p>
        <Link href={`/claims/${claimId}`}>
          <Button variant="ghost">Back to claim</Button>
        </Link>
      </div>
    );
  }

  const nodes = trace.nodes ?? [];

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link
          href={`/claims/${claimId}`}
          className="p-2 hover:bg-panel-3 rounded"
        >
          <ArrowLeft size={18} className="text-muted" />
        </Link>
        <div>
          <h1 className="text-xl font-bold font-mono">{trace.claim_id}</h1>
          <p className="text-sm text-muted">
            Node trace · {nodes.length} node{nodes.length === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      {/* Figures */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card">
          <div className="text-xs text-muted">Total time</div>
          <div className="text-xl font-bold font-mono mt-1">
            {trace.total_ms} ms
          </div>
        </div>
        <div className="card">
          <div className="text-xs text-muted">Total tokens</div>
          <div className="text-xl font-bold font-mono mt-1">
            {trace.total_tokens}
          </div>
        </div>
        <div className="card">
          <div className="text-xs text-muted">Nodes</div>
          <div className="text-xl font-bold font-mono mt-1">{nodes.length}</div>
        </div>
      </div>

      {/* Per-node table */}
      <div className="card">
        <h2 className="text-sm font-semibold mb-4">Per-node trace</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line text-muted-2 text-left">
                <th className="py-2 px-2">Node</th>
                <th className="py-2 px-2 text-right">Latency</th>
                <th className="py-2 px-2 text-right">Prompt</th>
                <th className="py-2 px-2 text-right">Completion</th>
                <th className="py-2 px-2 text-right">LLM calls</th>
                <th className="py-2 px-2 text-right">Cache hits</th>
                <th className="py-2 px-2 text-right">Attempts</th>
                <th className="py-2 px-2">Note</th>
                <th className="py-2 px-2">Error</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n, i) => (
                <tr
                  key={i}
                  className={cn(
                    "border-b border-line/50 hover:bg-panel-3",
                    isRepairNode(n.note, n.attempts) && "bg-amber-500/5"
                  )}
                >
                  <td className="py-2 px-2 font-mono text-accent">
                    {n.node}
                  </td>
                  <td className="py-2 px-2 text-right font-mono">
                    {n.latency_ms} ms
                  </td>
                  <td className="py-2 px-2 text-right font-mono">
                    {n.prompt_tokens}
                  </td>
                  <td className="py-2 px-2 text-right font-mono">
                    {n.completion_tokens}
                  </td>
                  <td className="py-2 px-2 text-right font-mono">
                    {n.llm_calls}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-settled">
                    {n.cache_hits}
                  </td>
                  <td className="py-2 px-2 text-right font-mono">
                    {n.attempts}
                  </td>
                  <td className="py-2 px-2 max-w-[200px] truncate text-muted">
                    {n.note || "—"}
                  </td>
                  <td className="py-2 px-2 max-w-[200px] truncate text-hospital">
                    {n.error || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import type { JobState } from "@/lib/types";

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

export function BatchList() {
  const router = useRouter();
  const { data: batches, isLoading } = useQuery({
    queryKey: ["batches"],
    queryFn: () => claimiqApi.listBatches(),
  });

  return (
    <div>
      <div className="card-head">
        <h2 className="text-sm font-semibold">Recent batches</h2>
      </div>

      {isLoading ? (
        <div className="card flex justify-center py-10">
          <Spinner />
        </div>
      ) : !batches || batches.length === 0 ? (
        <div className="card text-center py-10">
          <p className="text-muted">
            No batches yet — upload several bills to audit them together.
          </p>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-muted-2 text-left text-xs">
                <th className="py-2.5 px-3">Job</th>
                <th className="py-2.5 px-3">State</th>
                <th className="py-2.5 px-3">Progress</th>
                <th className="py-2.5 px-3 text-right">Total</th>
                <th className="py-2.5 px-3 text-right">Completed</th>
                <th className="py-2.5 px-3 text-right">Failed</th>
                <th className="py-2.5 px-3">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr
                  key={b.job_id}
                  onClick={() => router.push(`/batches/${b.job_id}`)}
                  className="border-b border-line/50 hover:bg-panel-3 cursor-pointer transition-colors"
                >
                  <td className="py-2.5 px-3 font-mono text-xs text-accent">
                    {b.job_id}
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge variant={stateVariant(b.state)}>{b.state}</Badge>
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex items-center gap-2">
                      <div className="w-24 h-1.5 bg-panel-3 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-accent rounded-full"
                          style={{ width: `${Math.round(b.progress * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted font-mono">
                        {Math.round(b.progress * 100)}%
                      </span>
                    </div>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-xs">
                    {b.total}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-xs">
                    {b.completed}
                  </td>
                  <td
                    className={
                      b.failed > 0
                        ? "py-2.5 px-3 text-right font-mono text-xs text-red-400"
                        : "py-2.5 px-3 text-right font-mono text-xs"
                    }
                  >
                    {b.failed}
                  </td>
                  <td className="py-2.5 px-3 text-muted text-xs">
                    {b.submitted_at?.slice(0, 10)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

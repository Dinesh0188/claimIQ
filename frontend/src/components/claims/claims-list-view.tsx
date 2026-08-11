"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { rupees } from "@/lib/utils";
import { Search } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function ClaimsListView() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [month, setMonth] = useState("");
  const [page, setPage] = useState(0);
  const limit = 25;

  const { data, isLoading } = useQuery({
    queryKey: ["claims", search, month, page],
    queryFn: () =>
      claimiqApi.claims({ q: search, month, limit, offset: page * limit }),
  });

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Claims</h1>
        <p className="text-muted mt-1">Previously audited claims.</p>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="relative flex-1 max-w-sm">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-2"
          />
          <input
            type="text"
            placeholder="Search by claim ID or diagnosis..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
            className="w-full bg-panel-3 border border-line rounded pl-9 pr-3 py-2 text-sm text-white placeholder:text-muted-2 focus:outline-none focus:border-accent"
          />
        </div>
        {data?.months && data.months.length > 0 && (
          <select
            value={month}
            onChange={(e) => {
              setMonth(e.target.value);
              setPage(0);
            }}
            className="bg-panel-3 border border-line rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-accent"
          >
            <option value="">All months</option>
            {data.months.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : !data || data.claims.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-muted">No claims found.</p>
          <p className="text-xs text-muted-2 mt-1">
            Run an audit from the Check a claim page to see it here.
          </p>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-muted-2 text-left text-xs">
                <th className="py-2.5 px-3">Claim ID</th>
                <th className="py-2.5 px-3">Date</th>
                <th className="py-2.5 px-3">Diagnosis</th>
                <th className="py-2.5 px-3 text-right">Gross</th>
                <th className="py-2.5 px-3 text-right">Settlement</th>
                <th className="py-2.5 px-3 text-right">Write-off</th>
                <th className="py-2.5 px-3">Gaps</th>
              </tr>
            </thead>
            <tbody>
              {data.claims.map((claim) => (
                <tr
                  key={claim.claim_id}
                  onClick={() => router.push(`/claims/${claim.claim_id}`)}
                  className="border-b border-line/50 hover:bg-panel-3 cursor-pointer transition-colors"
                >
                  <td className="py-2.5 px-3 font-mono text-xs text-accent">
                    {claim.claim_id}
                  </td>
                  <td className="py-2.5 px-3 text-muted text-xs">
                    {claim.audited_at?.slice(0, 10) || claim.month}
                  </td>
                  <td className="py-2.5 px-3 max-w-[200px] truncate">
                    {claim.diagnosis || "—"}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">
                    {rupees(claim.gross_bill)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-settled">
                    {rupees(claim.settlement)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-hospital">
                    {rupees(claim.hospital_writeoff)}
                  </td>
                  <td className="py-2.5 px-3">
                    {claim.doc_gap_count > 0 ? (
                      <Badge variant="warning">{claim.doc_gap_count}</Badge>
                    ) : (
                      <span className="text-muted-2">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          {data.total > limit && (
            <div className="flex items-center justify-between pt-4 border-t border-line mt-4">
              <span className="text-xs text-muted">
                Showing {page * limit + 1}–
                {Math.min((page + 1) * limit, data.total)} of {data.total}
              </span>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={(page + 1) * limit >= data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

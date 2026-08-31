"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { rupees, formatDate } from "@/lib/utils";
import { Search, FileSearch } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function ClaimsListView() {
  const router = useRouter();
  const getInitialParams = () => {
    if (typeof window === "undefined") return new URLSearchParams();
    return new URLSearchParams(window.location.search);
  };
  const [searchInput, setSearchInput] = useState(() => getInitialParams().get("q") ?? "");
  const [search, setSearch] = useState(() => getInitialParams().get("q") ?? "");
  const [month, setMonth] = useState(() => getInitialParams().get("month") ?? "");
  const [page, setPage] = useState(() => Number(getInitialParams().get("page") ?? "0"));
  const limit = 25;

  // Debounce search input (300ms) before committing to query
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Sync state to URL for persistence and back/forward navigation
  useEffect(() => {
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (month) params.set("month", month);
    if (page) params.set("page", String(page));
    const qs = params.toString();
    router.replace(`/claims${qs ? `?${qs}` : ""}`, { scroll: false });
  }, [search, month, page, router]);

  // Reset page when committed search or month changes (via debounce or select)
  useEffect(() => {
    // page reset already handled in debounce and onChange; this is for direct setSearch
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["claims", search, month, page],
    queryFn: ({ signal }) =>
      // pass AbortSignal so abandoned searches are cancelled
      claimiqApi.claims({ q: search, month, limit, offset: page * limit }, signal),
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
            aria-hidden="true"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-2"
          />
          <label htmlFor="claim-search" className="sr-only">
            Search claims
          </label>
          <input
            id="claim-search"
            name="q"
            type="search"
            autoComplete="off"
            spellCheck={false}
            aria-label="Search claims by ID or diagnosis"
            placeholder="Search by claim ID or diagnosis…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="w-full bg-panel-3 border border-line rounded pl-9 pr-3 py-2 text-sm text-white placeholder:text-muted-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </div>
        {data?.months && data.months.length > 0 && (
          <label htmlFor="claim-month-filter" className="sr-only">
            Filter by month
          </label>
        )}
        {data?.months && data.months.length > 0 && (
          <select
            id="claim-month-filter"
            name="month"
            aria-label="Filter claims by month"
            value={month}
            onChange={(e) => {
              setMonth(e.target.value);
              setPage(0);
            }}
            className="bg-panel-3 border border-line rounded px-3 py-2 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
        search || month ? (
          <div className="card text-center py-12">
            <p className="text-muted">No claims match your filters.</p>
            <p className="text-xs text-muted-2 mt-1">
              {search && month
                ? "Try a different search or month."
                : search
                ? "Try a different search."
                : "Try a different month."}
            </p>
          </div>
        ) : (
          <div className="card text-center py-12">
            <div className="flex justify-center mb-3">
              <div className="w-12 h-12 rounded-full bg-panel-3 flex items-center justify-center">
                <FileSearch size={20} className="text-muted" />
              </div>
            </div>
            <p className="text-muted">No audited claims yet.</p>
            <p className="text-xs text-muted-2 mt-1">
              Run an audit from the Check a claim page to see it here.
            </p>
            <Link
              href="/"
              className="inline-flex items-center justify-center gap-2 font-medium rounded-md bg-accent hover:bg-accent-deep text-white px-4 py-2 text-sm mt-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Check a claim
            </Link>
          </div>
        )
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
                  className="border-b border-line/50 hover:bg-panel-3 transition-colors"
                >
                  <td className="py-2.5 px-3 font-mono text-xs">
                    <Link
                      href={`/claims/${claim.claim_id}`}
                      className="text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                    >
                      {claim.claim_id}
                    </Link>
                  </td>
                  <td className="py-2.5 px-3 text-muted text-xs tabular-nums">
                    {claim.audited_at ? formatDate(claim.audited_at) : claim.month}
                  </td>
                  <td className="py-2.5 px-3 max-w-[200px] truncate min-w-0">
                    {claim.diagnosis || "—"}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono tabular-nums">
                    {rupees(claim.gross_bill)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-settled tabular-nums">
                    {rupees(claim.settlement)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-hospital tabular-nums">
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

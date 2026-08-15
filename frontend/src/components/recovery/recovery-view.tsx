"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { rupees, num } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";

export function RecoveryView() {
  const [draft, setDraft] = useState("");
  const [volume, setVolume] = useState(0);
  const lastSynced = useRef<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["recovery", volume],
    queryFn: () => claimiqApi.recovery(volume),
  });

  useEffect(() => {
    if (data && !data.empty && lastSynced.current !== volume) {
      lastSynced.current = volume;
      setDraft(String(data.annual_claim_volume));
    }
  }, [data, volume]);

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (!data || data.empty) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold mb-2">Recovery projection</h1>
        <div className="card text-center py-12">
          <p className="text-muted">
            {data?.reason || "No audited claims yet."}
          </p>
          <p className="text-xs text-muted-2 mt-1">
            Audit some claims from the Check a claim page to see the recovery
            projection here.
          </p>
        </div>
      </div>
    );
  }

  const commit = () => {
    const parsed = Math.round(Number(draft));
    const next = Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
    setVolume(next);
  };

  const volumeDisclosure =
    data.volume_source === "supplied by you"
      ? "Projected from the annual volume you supplied"
      : "Projected from observed run rate";

  const top = data.items.slice(0, 3);
  const rest = data.items.slice(3);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Recovery projection</h1>
        <p className="text-muted mt-1">
          What fixing the billing master is worth per year, and what to fix
          first.
        </p>
      </div>

      {/* Header figures */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard
          label="Annual recovery"
          value={rupees(data.annual_recovery)}
          valueClass="text-settled"
          sub={volumeDisclosure}
        />
        <StatCard label="Leak per claim" value={rupees(data.leak_per_claim)} />
        <StatCard
          label="Leak rate"
          value={`${num(data.leak_rate_pct).toFixed(2)}%`}
        />
        <StatCard
          label="Claims audited"
          value={String(data.claims_audited)}
        />
        <StatCard
          label="Months observed"
          value={String(data.months_observed)}
        />
      </div>

      {/* Volume input */}
      <div className="card flex items-end gap-4 flex-wrap">
        <div className="flex-1 min-w-[220px]">
          <label
            htmlFor="annual-claim-volume"
            className="text-sm font-medium text-muted"
          >
            Annual claim volume (optional)
          </label>
          <p className="text-xs text-muted-2 mt-0.5">
            Leave blank to extrapolate from the observed run rate.
          </p>
          <input
            id="annual-claim-volume"
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="e.g. 1200"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              }
            }}
            className="mt-2 w-full bg-panel-3 border border-line rounded px-3 py-2 text-sm text-white placeholder:text-muted-2 focus:outline-none focus:border-accent"
          />
        </div>
        <div className="text-xs text-muted-2 pb-2">
          Effective:{" "}
          <span className="font-mono text-muted">
            {data.annual_claim_volume.toLocaleString()}
          </span>{" "}
          claims/year · {data.volume_source}
        </div>
      </div>

      {/* Top three */}
      <div className="card">
        <h2 className="text-sm font-semibold">Fix these first</h2>
        <p className="text-xs text-muted-2 mt-1">
          The top three items carry most of the money and all of the momentum.
        </p>
        <ol className="mt-3 divide-y divide-line/50">
          {top.map((item, i) => (
            <li key={i} className="flex items-center gap-4 py-3">
              <span className="text-lg font-mono text-accent w-6 shrink-0">
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-sm capitalize truncate">{item.item}</div>
                <div className="text-xs text-muted-2 mt-0.5">
                  {item.claims_affected}{" "}
                  {item.claims_affected === 1 ? "claim" : "claims"} · on{" "}
                  {num(item.incidence_pct).toFixed(0)}% of claims
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-mono font-bold text-settled">
                  {rupees(item.annual_recovery)}/yr
                </div>
                <div className="text-xs text-muted-2">
                  {rupees(item.leak_per_claim)}/claim
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>

      {/* Full items list */}
      {rest.length > 0 && (
        <details className="card group">
          <summary className="cursor-pointer text-sm font-medium select-none">
            More remediable items ({rest.length})
          </summary>
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-muted-2 text-left text-xs">
                  <th className="py-2 px-3">Item</th>
                  <th className="py-2 px-3 text-right">Claims</th>
                  <th className="py-2 px-3 text-right">Incidence</th>
                  <th className="py-2 px-3 text-right">Leak / claim</th>
                  <th className="py-2 px-3 text-right">Annual recovery</th>
                </tr>
              </thead>
              <tbody>
                {rest.map((item, i) => (
                  <tr
                    key={i}
                    className="border-b border-line/50 hover:bg-panel-3"
                  >
                    <td className="py-2.5 px-3 capitalize">{item.item}</td>
                    <td className="py-2.5 px-3 text-right text-muted">
                      {item.claims_affected}
                    </td>
                    <td className="py-2.5 px-3 text-right text-muted">
                      {num(item.incidence_pct).toFixed(1)}%
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-muted">
                      {rupees(item.leak_per_claim)}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono text-settled">
                      {rupees(item.annual_recovery)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* Assumptions */}
      {data.assumptions.length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold mb-3">Assumptions</h2>
          <ul className="space-y-2">
            {data.assumptions.map((a, i) => (
              <li key={i} className="flex gap-2 text-sm text-muted">
                <span className="text-muted-2 shrink-0">•</span>
                <span>{a}</span>
              </li>
            ))}
          </ul>
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

"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { Spinner } from "@/components/ui/spinner";
import { Badge } from "@/components/ui/badge";
import { Search, AlertTriangle } from "lucide-react";

const LIST_LABELS: Record<string, string> = {
  LIST_I_OPTIONAL: "List I — Optional (patient pays)",
  LIST_II_ROOM: "List II — Subsumed in room",
  LIST_III_PROCEDURE: "List III — Subsumed in procedure",
  LIST_IV_TREATMENT: "List IV — Subsumed in treatment",
  POLICY_WORDING: "Policy wording",
};

export function RulesCatalogView() {
  const [search, setSearch] = useState("");
  const [activeList, setActiveList] = useState<string | "all">("all");

  const { data: catalog, isLoading } = useQuery({
    queryKey: ["rules-catalog"],
    queryFn: () => claimiqApi.rulesCatalog(true),
  });

  const filteredRules = useMemo(() => {
    if (!catalog?.rules) return [];
    let rules = catalog.rules;

    if (activeList !== "all") {
      rules = rules.filter((r) => (r.list_name || "POLICY_WORDING") === activeList);
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      rules = rules.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          r.body.toLowerCase().includes(q) ||
          r.chunk_id.toLowerCase().includes(q) ||
          r.aliases?.some((a) => a.toLowerCase().includes(q))
      );
    }

    return rules;
  }, [catalog, search, activeList]);

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  if (!catalog) {
    return (
      <div className="card text-center py-12">
        <p className="text-red-400">Could not load the rule catalog.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Rule Catalog</h1>
        <p className="text-muted mt-1">
          {catalog.total} rules · corpus {catalog.corpus_version}
        </p>
      </div>

      {/* Verification status */}
      {!catalog.verified_on && (
        <div className="flex items-start gap-3 p-3 bg-amber-500/5 border border-amber-500/20 rounded text-sm text-amber-400">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <div>
            <strong>Unverified snapshot.</strong>{" "}
            <span className="text-muted">
              {catalog.note}
            </span>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="relative flex-1 max-w-sm">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-2"
          />
          <input
            type="text"
            placeholder="Search rules..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-panel-3 border border-line rounded pl-9 pr-3 py-2 text-sm text-white placeholder:text-muted-2 focus:outline-none focus:border-accent"
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          <FilterButton
            active={activeList === "all"}
            onClick={() => setActiveList("all")}
          >
            All ({catalog.total})
          </FilterButton>
          {Object.entries(catalog.counts).map(([key, count]) => (
            <FilterButton
              key={key}
              active={activeList === key}
              onClick={() => setActiveList(key)}
            >
              {key.replace("LIST_", "L").replace(/_/g, " ")} ({count})
            </FilterButton>
          ))}
        </div>
      </div>

      {/* Rules list */}
      <div className="space-y-2">
        {filteredRules.length === 0 ? (
          <div className="card text-center py-8">
            <p className="text-muted">
              No rules match your search.
            </p>
          </div>
        ) : (
          filteredRules.map((rule) => (
            <div key={rule.chunk_id} className="card hover:border-line-2 transition-colors">
              <div className="flex items-start gap-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-xs text-accent">
                      {rule.chunk_id}
                    </span>
                    <Badge variant="muted">
                      {LIST_LABELS[rule.list_name || "POLICY_WORDING"]?.split("—")[0]?.trim() ||
                        rule.list_name}
                    </Badge>
                  </div>
                  <h3 className="font-semibold text-sm">{rule.title}</h3>
                  <p className="text-sm text-muted mt-1 line-clamp-2">
                    {rule.body}
                  </p>
                  {rule.aliases && rule.aliases.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {rule.aliases.slice(0, 5).map((a, i) => (
                        <span
                          key={i}
                          className="text-xs bg-panel-3 px-1.5 py-0.5 rounded text-muted"
                        >
                          {a}
                        </span>
                      ))}
                      {rule.aliases.length > 5 && (
                        <span className="text-xs text-muted-2">
                          +{rule.aliases.length - 5} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Count */}
      <p className="text-xs text-muted-2 text-center">
        Showing {filteredRules.length} of {catalog.total} rules
      </p>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
        active
          ? "bg-accent/10 text-accent border border-accent/30"
          : "bg-panel-3 text-muted border border-line hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

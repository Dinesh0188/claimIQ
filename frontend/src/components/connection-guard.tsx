"use client";

import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "./ui/button";

/**
 * Shows a full-page error when the backend is unreachable.
 * Wraps the main content in the AppShell.
 */
export function ConnectionGuard({ children }: { children: React.ReactNode }) {
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["health"],
    queryFn: claimiqApi.health,
    retry: 2,
    retryDelay: 1000,
  });

  if (isLoading) return <>{children}</>;

  if (error && !data) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-4">
        <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-6">
          <AlertTriangle size={32} className="text-red-400" />
        </div>
        <h1 className="text-xl font-bold mb-2">Cannot reach the ClaimIQ service</h1>
        <p className="text-sm text-muted max-w-md mb-2">
          {(error as Error).message}
        </p>
        <p className="text-xs text-muted-2 mb-6">
          Start the backend with <code className="bg-panel-3 px-1.5 py-0.5 rounded">.\start.ps1</code> or run{" "}
          <code className="bg-panel-3 px-1.5 py-0.5 rounded">
            python -m uvicorn claimiq.api:app --port 8000
          </code>
        </p>
        <Button variant="primary" onClick={() => refetch()}>
          <RefreshCw size={14} /> Try again
        </Button>
      </div>
    );
  }

  return <>{children}</>;
}

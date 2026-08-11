"use client";

import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { Spinner } from "@/components/ui/spinner";
import type { ClaimPacket } from "@/lib/types";
import { useState } from "react";

const LABELS: Record<string, string> = {
  billing_error: "Billing errors",
  cardiac: "Room-rent trap",
  clean: "Clean claim",
  incomplete: "Missing documents",
};

interface SamplesCardProps {
  onAudit: (packet: ClaimPacket) => void;
}

export function SamplesCard({ onAudit }: SamplesCardProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState<string | null>(null);

  const { data: samples } = useQuery({
    queryKey: ["samples"],
    queryFn: claimiqApi.samples,
  });

  const handleClick = async (name: string) => {
    setLoading(name);
    try {
      const packet = await claimiqApi.getSample(name);
      onAudit(packet);
    } catch (err) {
      toast(`Could not load sample: ${(err as Error).message}`, "error");
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h2 className="text-sm font-semibold">No documents to hand?</h2>
          <p className="text-xs text-muted mt-0.5">
            Audit a worked example to see what the output looks like.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        {samples?.map((name) => (
          <Button
            key={name}
            onClick={() => handleClick(name)}
            disabled={loading !== null}
          >
            {loading === name && <Spinner className="w-3 h-3" />}
            {LABELS[name] || name.replace(/_/g, " ")}
          </Button>
        )) ?? (
          <span className="text-sm text-muted">Loading samples...</span>
        )}
      </div>
    </div>
  );
}

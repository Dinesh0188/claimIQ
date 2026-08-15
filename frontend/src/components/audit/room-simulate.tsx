"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { rupees } from "@/lib/utils";
import { ChevronLeft } from "lucide-react";
import type { ClaimPacket, WaterfallResult } from "@/lib/types";

interface RoomSimulateProps {
  packet: ClaimPacket;
  profile: string;
  onApply: (wf: WaterfallResult) => void;
  onReset: () => void;
}

export function RoomSimulate({
  packet,
  profile,
  onApply,
  onReset,
}: RoomSimulateProps) {
  const { toast } = useToast();
  const [simulated, setSimulated] = useState<{
    gain: string;
    wf: WaterfallResult;
  } | null>(null);
  const [notApplicable, setNotApplicable] = useState(false);

  const mutation = useMutation({
    mutationFn: () => claimiqApi.simulateRoom(packet, profile),
    onSuccess: (data) => {
      setNotApplicable(false);
      if (!data.applicable || !data.result) {
        setNotApplicable(true);
        onReset();
        return;
      }
      setSimulated({ gain: data.gain ?? "0", wf: data.result });
      onApply(data.result);
    },
    onError: (err) => {
      toast(`Room simulation failed: ${(err as Error).message}`, "error");
    },
  });

  const handleRun = () => {
    if (mutation.isPending) return;
    setSimulated(null);
    setNotApplicable(false);
    mutation.mutate();
  };

  const handleBack = () => {
    setSimulated(null);
    setNotApplicable(false);
    onReset();
  };

  return (
    <div className="card border border-accent/20 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Room within cap</h2>
          <p className="text-xs text-muted mt-0.5">
            What if the room had been billed inside the policy cap?
          </p>
        </div>
        {simulated ? (
          <Button size="sm" variant="ghost" onClick={handleBack}>
            <ChevronLeft size={14} /> Back to original
          </Button>
        ) : (
          <Button
            size="sm"
            variant="primary"
            onClick={handleRun}
            disabled={mutation.isPending}
          >
            {mutation.isPending && <Spinner className="w-4 h-4" />}
            Try a room within cap
          </Button>
        )}
      </div>

      {mutation.isPending && (
        <p className="text-xs text-muted">Recomputing the waterfall...</p>
      )}

      {notApplicable && (
        <p className="text-xs text-amber-400">
          Room already within cap — no downgrade available.
        </p>
      )}

      {simulated && (
        <div className="border-t border-line pt-3 space-y-1">
          <p className="text-xs font-semibold text-accent">
            What-if simulation — not persisted
          </p>
          <p className="text-sm text-muted">
            A room within the policy limit would settle{" "}
            <strong className="font-mono text-settled">
              {rupees(simulated.gain)} more
            </strong>
            . The figures below reflect the downgraded stay.
          </p>
        </div>
      )}
    </div>
  );
}

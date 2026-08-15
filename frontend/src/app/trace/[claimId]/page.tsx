"use client";

import { useParams } from "next/navigation";
import { TraceView } from "@/components/trace/trace-view";

export default function TracePage() {
  const params = useParams<{ claimId: string }>();
  return <TraceView claimId={params.claimId} />;
}

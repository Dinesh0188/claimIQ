"use client";

import { useParams } from "next/navigation";
import { BatchDetail } from "@/components/batches/batch-detail";

export default function BatchDetailPage() {
  const params = useParams();
  const jobId = params.jobId as string;
  return <BatchDetail jobId={jobId} />;
}

"use client";

import { useParams } from "next/navigation";
import { ClaimDetailView } from "@/components/claims/claim-detail-view";

export default function ClaimDetailPage() {
  const params = useParams();
  const claimId = params.claimId as string;
  return <ClaimDetailView claimId={claimId} />;
}

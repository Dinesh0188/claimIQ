"use client";

import { BatchCreate } from "@/components/batches/batch-create";
import { BatchList } from "@/components/batches/batch-list";

export default function BatchesPage() {
  return (
    <div className="max-w-6xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Batches</h1>
        <p className="text-muted mt-1">
          Audit many bills at once and review the portfolio outcome.
        </p>
      </div>
      <BatchCreate />
      <BatchList />
    </div>
  );
}

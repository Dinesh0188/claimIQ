import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect } from "vitest";
import fixture from "./fixtures/audit-result.json";
import { AuditResultView } from "@/components/audit/audit-result-view";
import type { AuditResult, ClaimPacket } from "@/lib/types";

const packet: ClaimPacket = {
  claim_id: "CL-2025-0137",
  context: {
    claim_type: "cashless",
    admission_date: "2025-03-11",
    discharge_date: "2025-03-14",
    primary_diagnosis: "Acute pancreatitis",
    procedure_performed: null,
    involves_implant: false,
    preauth_approved_amount: "950000",
    documents_attached: ["admission", "discharge", "invoice"],
  },
  policy: {
    policy_id: "POL-8842",
    sum_insured: "1000000",
    balance_sum_insured: "640000",
    room_rent_cap_per_day: "7500",
    icu_cap_per_day: "15000",
    copay_percent: "10",
    deductible: "0",
    procedure_sublimits: {},
  },
  room_stay: {
    room_category: "DELUXE",
    rate_per_day: "12000",
    days: 3,
    is_icu: false,
  },
  line_items: [
    {
      line_no: 1,
      description: "Room rent day 1",
      head: "ROOM",
      quantity: "1",
      unit_rate: "12000",
      amount: "12000",
      service_date: "2025-03-11",
      extract_confidence: 0.98,
    },
  ],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <AuditResultView
        result={fixture as unknown as AuditResult}
        packet={packet}
        profile="typical"
      />
    </QueryClientProvider>
  );
}

describe("audit result wire shape", () => {
  it("renders the money figures derived from the fixture", () => {
    renderView();
    expect(screen.getByText("Likely settlement")).toBeTruthy();
    expect(screen.getByText("₹8,50,000")).toBeTruthy();
    expect(screen.getByText("Gross bill")).toBeTruthy();
    expect(screen.getAllByText("Hospital absorbs").length).toBeGreaterThan(0);
    expect(screen.getByText("92% assessed")).toBeTruthy();
  });

  it("renders the per-profile settlement range and verdict", () => {
    renderView();
    expect(screen.getByText(/Range ₹8,20,000 – ₹8,80,000/)).toBeTruthy();
    expect(screen.getByText("Needs attention")).toBeTruthy();
    expect(screen.getByText("Findings (2)")).toBeTruthy();
  });
});

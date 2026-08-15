import { num, today, shiftDays } from "./utils";
import type { ClaimPacket, ExtractionResult } from "./types";

export interface ClaimFormFields {
  sum_insured: string;
  room_cap: string;
  copay: string;
  claim_type: "cashless" | "reimbursement";
  room_category: string;
  room_rate: string;
  room_days: string;
  is_icu: boolean;
  admission: string;
  discharge: string;
  diagnosis: string;
  procedure: string;
  preauth: string;
  implant: boolean;
}

/**
 * Build a ClaimPacket from extracted documents. When `form` is omitted (or a field
 * is left undefined) the same defaults the single-audit form pre-fills are used, so
 * the batch flow produces identical packets without showing the form.
 */
export function buildPacketFromUploads(
  uploads: ExtractionResult[],
  form?: Partial<ClaimFormFields>
): ClaimPacket {
  const lineItems = uploads.flatMap((u) => u.line_items || []);
  const policy = uploads.find((u) => u.policy_terms)?.policy_terms || null;
  const clinical = uploads.find((u) => u.clinical_context)?.clinical_context || null;
  const stay = uploads.find((u) => u.room_stay)?.room_stay || null;
  const hasImplant = uploads.some((u) => u.has_implant);

  const days = stay?.days || 0;
  const discharge = clinical?.discharge_date || today();
  const admission =
    clinical?.admission_date || shiftDays(discharge, -Math.max(days, 1));

  const f: ClaimFormFields = {
    sum_insured: form?.sum_insured ?? (policy?.sum_insured || "500000"),
    room_cap: form?.room_cap ?? (policy?.room_rent_cap_per_day || "6000"),
    copay: form?.copay ?? (policy?.copay_percent || "10"),
    claim_type: form?.claim_type ?? "cashless",
    room_category: form?.room_category ?? (stay?.room_category || ""),
    room_rate: form?.room_rate ?? (stay?.rate_per_day || "0"),
    room_days: form?.room_days ?? String(days),
    is_icu: form?.is_icu ?? (stay?.is_icu || false),
    admission: form?.admission ?? admission,
    discharge: form?.discharge ?? discharge,
    diagnosis: form?.diagnosis ?? (clinical?.primary_diagnosis || ""),
    procedure: form?.procedure ?? (clinical?.procedure_performed || ""),
    preauth: form?.preauth ?? "0",
    implant: form?.implant ?? hasImplant,
  };

  const name = (uploads[0]?.filename || "upload").replace(/\.[^.]+$/, "");

  return {
    claim_id: `UPLOAD-${name}`.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 40),
    context: {
      claim_type: f.claim_type,
      admission_date: f.admission,
      discharge_date: f.discharge,
      primary_diagnosis: f.diagnosis.trim(),
      procedure_performed: f.procedure.trim() || null,
      involves_implant: f.implant,
      preauth_approved_amount: num(f.preauth) ? f.preauth : null,
      documents_attached: uploads
        .map((u) => u.document_id)
        .filter(Boolean) as string[],
    },
    policy: {
      policy_id: policy?.policy_id || "not stated",
      sum_insured: f.sum_insured,
      balance_sum_insured: f.sum_insured,
      room_rent_cap_per_day: num(f.room_cap) ? f.room_cap : null,
      icu_cap_per_day: policy?.icu_cap_per_day || null,
      copay_percent: f.copay,
      deductible: "0",
      procedure_sublimits: {},
    },
    room_stay: {
      room_category: f.room_category.trim() || "Room",
      rate_per_day: f.room_rate,
      days: Math.round(num(f.room_days)),
      is_icu: f.is_icu,
    },
    line_items: lineItems.map((it, i) => ({ ...it, line_no: i + 1 })),
  };
}

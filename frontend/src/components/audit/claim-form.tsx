"use client";

import { useState, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle, CheckCircle } from "lucide-react";
import { num, today, shiftDays } from "@/lib/utils";
import type { ClaimPacket, ExtractionResult } from "@/lib/types";

interface ClaimFormProps {
  uploads: ExtractionResult[];
  onSubmit: (packet: ClaimPacket) => void;
  disabled?: boolean;
}

function FormField({
  label,
  name,
  sourced,
  hint,
  children,
}: {
  label: string;
  name: string;
  sourced?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={`f-${name}`} className="text-xs text-muted flex items-center gap-2">
        {label}
        {sourced && (
          <span className="text-settled flex items-center gap-1">
            <CheckCircle size={10} /> from document
          </span>
        )}
      </label>
      {children}
      {hint && <p className="text-xs text-muted-2">{hint}</p>}
    </div>
  );
}

export function ClaimForm({ uploads, onSubmit, disabled }: ClaimFormProps) {
  const lineItems = useMemo(
    () => uploads.flatMap((u) => u.line_items || []),
    [uploads]
  );
  const policy = uploads.find((u) => u.policy_terms)?.policy_terms || null;
  const clinical = uploads.find((u) => u.clinical_context)?.clinical_context || null;
  const stay = uploads.find((u) => u.room_stay)?.room_stay || null;
  const hasImplant = uploads.some((u) => u.has_implant);

  const [formData, setFormData] = useState(() => {
    const days = stay?.days || 0;
    const discharge = clinical?.discharge_date || today();
    const admission = clinical?.admission_date || shiftDays(discharge, -Math.max(days, 1));

    return {
      sum_insured: policy?.sum_insured || "500000",
      room_cap: policy?.room_rent_cap_per_day || "6000",
      copay: policy?.copay_percent || "10",
      claim_type: "cashless" as "cashless" | "reimbursement",
      room_category: stay?.room_category || "",
      room_rate: stay?.rate_per_day || "0",
      room_days: String(days),
      is_icu: stay?.is_icu || false,
      admission,
      discharge,
      diagnosis: clinical?.primary_diagnosis || "",
      procedure: clinical?.procedure_performed || "",
      preauth: "0",
      implant: hasImplant,
    };
  });

  const update = (field: string, value: string | boolean) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const inputClass =
    "w-full bg-panel-3 border border-line rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-accent";

  const blockers = useMemo(() => {
    const problems: string[] = [];
    if (!lineItems.length)
      problems.push("No itemised bill among these documents — add the hospital bill.");
    if (num(formData.room_rate) <= 0 || num(formData.room_days) <= 0)
      problems.push("Enter the room rate and number of room days.");
    if (!formData.diagnosis.trim())
      problems.push("Enter the primary diagnosis — it drives the document checklist.");
    if (formData.admission && formData.discharge && formData.discharge < formData.admission)
      problems.push("The discharge date is before the admission date.");
    return problems;
  }, [formData, lineItems]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (blockers.length) return;

    const name = (uploads[0]?.filename || "upload").replace(/\.[^.]+$/, "");

    const packet: ClaimPacket = {
      claim_id: `UPLOAD-${name}`.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 40),
      context: {
        claim_type: formData.claim_type,
        admission_date: formData.admission,
        discharge_date: formData.discharge,
        primary_diagnosis: formData.diagnosis.trim(),
        procedure_performed: formData.procedure.trim() || null,
        involves_implant: formData.implant,
        preauth_approved_amount: num(formData.preauth) ? formData.preauth : null,
        documents_attached: uploads
          .map((u) => u.document_id)
          .filter(Boolean) as string[],
      },
      policy: {
        policy_id: policy?.policy_id || "not stated",
        sum_insured: formData.sum_insured,
        balance_sum_insured: formData.sum_insured,
        room_rent_cap_per_day: num(formData.room_cap) ? formData.room_cap : null,
        icu_cap_per_day: policy?.icu_cap_per_day || null,
        copay_percent: formData.copay,
        deductible: "0",
        procedure_sublimits: {},
      },
      room_stay: {
        room_category: formData.room_category.trim() || "Room",
        rate_per_day: formData.room_rate,
        days: Math.round(num(formData.room_days)),
        is_icu: formData.is_icu,
      },
      line_items: lineItems.map((it, i) => ({ ...it, line_no: i + 1 })),
    };

    onSubmit(packet);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Policy terms */}
      <div className="card">
        <div className="card-head">
          <h2 className="text-sm font-semibold">Policy terms</h2>
          <span className="text-xs text-muted">
            {policy
              ? "Read from your policy schedule — correct anything out of date."
              : "No policy schedule uploaded, so these are defaults."}
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <FormField label="Sum insured (₹)" name="sum_insured" sourced={!!policy?.sum_insured}>
            <input
              type="number"
              id="f-sum_insured"
              className={inputClass}
              value={formData.sum_insured}
              onChange={(e) => update("sum_insured", e.target.value)}
              min={0}
              step={10000}
            />
          </FormField>
          <FormField label="Room limit (₹/day)" name="room_cap" sourced={!!policy?.room_rent_cap_per_day} hint="0 = no room limit">
            <input
              type="number"
              id="f-room_cap"
              className={inputClass}
              value={formData.room_cap}
              onChange={(e) => update("room_cap", e.target.value)}
              min={0}
              step={500}
            />
          </FormField>
          <FormField label="Co-pay (%)" name="copay" sourced={!!policy?.copay_percent}>
            <input
              type="number"
              id="f-copay"
              className={inputClass}
              value={formData.copay}
              onChange={(e) => update("copay", e.target.value)}
              min={0}
              max={100}
              step={0.5}
            />
          </FormField>
          <FormField label="Claim type" name="claim_type">
            <select
              id="f-claim_type"
              className={inputClass}
              value={formData.claim_type}
              onChange={(e) => update("claim_type", e.target.value)}
            >
              <option value="cashless">Cashless</option>
              <option value="reimbursement">Reimbursement</option>
            </select>
          </FormField>
        </div>
      </div>

      {/* Stay & clinical */}
      <div className="card">
        <div className="card-head">
          <h2 className="text-sm font-semibold">Stay and clinical details</h2>
          <span className="text-xs text-muted">
            {stay ? "Room read from the bill." : "No room charge found — enter the stay."}
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <FormField label="Room category" name="room_category" sourced={!!stay}>
            <input
              id="f-room_category"
              className={inputClass}
              value={formData.room_category}
              onChange={(e) => update("room_category", e.target.value)}
              placeholder="Single AC / ICU"
            />
          </FormField>
          <FormField label="Room rate (₹/day)" name="room_rate" sourced={!!stay}>
            <input
              type="number"
              id="f-room_rate"
              className={inputClass}
              value={formData.room_rate}
              onChange={(e) => update("room_rate", e.target.value)}
              min={0}
              step={100}
            />
          </FormField>
          <FormField label="Room days" name="room_days" sourced={!!stay}>
            <input
              type="number"
              id="f-room_days"
              className={inputClass}
              value={formData.room_days}
              onChange={(e) => update("room_days", e.target.value)}
              min={0}
              max={365}
            />
          </FormField>
          <FormField label="ICU stay" name="is_icu">
            <div className="flex items-center gap-2 h-[38px]">
              <input
                type="checkbox"
                id="f-is_icu"
                checked={formData.is_icu}
                onChange={(e) => update("is_icu", e.target.checked)}
                className="w-4 h-4 accent-accent"
              />
              <span className="text-xs text-muted">Applies the ICU limit</span>
            </div>
          </FormField>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
          <FormField label="Admission" name="admission" sourced={!!clinical?.admission_date}>
            <input
              type="date"
              id="f-admission"
              className={inputClass}
              value={formData.admission}
              onChange={(e) => update("admission", e.target.value)}
            />
          </FormField>
          <FormField label="Discharge" name="discharge" sourced={!!clinical?.discharge_date}>
            <input
              type="date"
              id="f-discharge"
              className={inputClass}
              value={formData.discharge}
              onChange={(e) => update("discharge", e.target.value)}
            />
          </FormField>
          <FormField label="Primary diagnosis" name="diagnosis" sourced={!!clinical?.primary_diagnosis}>
            <input
              id="f-diagnosis"
              className={inputClass}
              value={formData.diagnosis}
              onChange={(e) => update("diagnosis", e.target.value)}
              placeholder="Required"
            />
          </FormField>
          <FormField label="Procedure" name="procedure" sourced={!!clinical?.procedure_performed}>
            <input
              id="f-procedure"
              className={inputClass}
              value={formData.procedure}
              onChange={(e) => update("procedure", e.target.value)}
              placeholder="Blank if medical"
            />
          </FormField>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
          <FormField label="Pre-authorised (₹)" name="preauth" hint="0 = none issued">
            <input
              type="number"
              id="f-preauth"
              className={inputClass}
              value={formData.preauth}
              onChange={(e) => update("preauth", e.target.value)}
              min={0}
              step={10000}
            />
          </FormField>
          <FormField label="Implant used" name="implant">
            <div className="flex items-center gap-2 h-[38px]">
              <input
                type="checkbox"
                id="f-implant"
                checked={formData.implant}
                onChange={(e) => update("implant", e.target.checked)}
                className="w-4 h-4 accent-accent"
              />
              <span className="text-xs text-muted">Drives the invoice requirement</span>
            </div>
          </FormField>
        </div>
      </div>

      {/* Blockers & submit */}
      <div className="card">
        {blockers.length > 0 && (
          <div className="space-y-2 mb-4">
            {blockers.map((b, i) => (
              <div
                key={i}
                className="flex items-start gap-2 p-3 bg-amber-500/5 border border-amber-500/20 rounded text-sm text-amber-400"
              >
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                {b}
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center gap-4">
          <Button
            type="submit"
            variant="primary"
            disabled={blockers.length > 0 || disabled}
          >
            Run audit
          </Button>
          <span className="text-xs text-muted">
            {lineItems.length} line items ·{" "}
            {uploads.filter((u) => u.document_id).length} documents recognised
          </span>
        </div>
      </div>
    </form>
  );
}

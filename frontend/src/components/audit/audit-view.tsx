"use client";

import { useState } from "react";
import { useProfile } from "@/components/profile-selector";
import { claimiqApi } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import type {
  AuditResult,
  BillLineItem,
  ClaimPacket,
  ExtractionResult,
} from "@/lib/types";
import { UploadZone } from "./upload-zone";
import { FileList } from "./file-list";
import { ClaimForm } from "./claim-form";
import { AuditResultView } from "./audit-result-view";
import { SamplesCard } from "./samples-card";
import { Spinner } from "@/components/ui/spinner";

export function AuditView() {
  const { profile } = useProfile();
  const { toast } = useToast();
  const [uploads, setUploads] = useState<ExtractionResult[]>([]);
  const [result, setResult] = useState<AuditResult | null>(null);
  const [packet, setPacket] = useState<ClaimPacket | null>(null);
  const [uploading, setUploading] = useState(false);
  const [auditing, setAuditing] = useState(false);

  const handleFilesUploaded = async (files: File[]) => {
    if (!files.length || uploading) return;
    setUploading(true);
    const results: ExtractionResult[] = [];
    const failures: string[] = [];

    for (const file of files) {
      try {
        const extraction = await claimiqApi.extract(file);
        results.push(extraction);
      } catch (err) {
        failures.push(`${file.name}: ${(err as Error).message}`);
      }
    }

    setUploads(results);
    setResult(null);
    setUploading(false);

    for (const f of failures) {
      toast(f, "error");
    }
  };

  const handleRunAudit = async (claimPacket: ClaimPacket) => {
    setAuditing(true);
    setPacket(claimPacket);
    try {
      const auditResult = await claimiqApi.audit(claimPacket);
      setResult(auditResult);
    } catch (err) {
      toast(`Audit failed: ${(err as Error).message}`, "error");
    } finally {
      setAuditing(false);
    }
  };

  const handleSampleAudit = async (samplePacket: ClaimPacket) => {
    setUploads([]);
    setAuditing(true);
    setPacket(samplePacket);
    try {
      const auditResult = await claimiqApi.audit(samplePacket);
      setResult(auditResult);
    } catch (err) {
      toast(`Audit failed: ${(err as Error).message}`, "error");
    } finally {
      setAuditing(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold">Check a claim</h1>
        <p className="text-muted mt-1">
          Read the packet before it goes to the TPA.
        </p>
      </div>

      {/* Upload zone */}
      <UploadZone onFiles={handleFilesUploaded} disabled={uploading} />

      {/* Uploading state */}
      {uploading && (
        <div className="card flex items-center gap-3">
          <Spinner />
          <span className="text-sm text-muted">Reading documents...</span>
        </div>
      )}

      {/* File list */}
      {uploads.length > 0 && <FileList uploads={uploads} />}

      {/* Claim form or samples */}
      {uploads.length > 0 ? (
        <ClaimForm
          uploads={uploads}
          onSubmit={handleRunAudit}
          disabled={auditing}
        />
      ) : (
        !result && !auditing && <SamplesCard onAudit={handleSampleAudit} />
      )}

      {/* Auditing progress */}
      {auditing && (
        <div className="card space-y-3">
          <div className="flex items-center gap-3">
            <Spinner />
            <span className="text-sm">Running audit...</span>
          </div>
          <div className="flex gap-2">
            {["Classifying charges", "Computing deductions", "Verifying numbers"].map(
              (step, i) => (
                <div
                  key={step}
                  className="flex items-center gap-2 text-xs text-muted"
                >
                  <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                  {step}
                </div>
              )
            )}
          </div>
        </div>
      )}

      {/* Results */}
      {result && (
        <AuditResultView
          result={result}
          packet={packet!}
          profile={profile}
        />
      )}
    </div>
  );
}

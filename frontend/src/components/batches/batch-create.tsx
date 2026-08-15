"use client";

import { useState } from "react";
import { useDropzone } from "react-dropzone";
import { useRouter } from "next/navigation";
import { claimiqApi } from "@/lib/api";
import { buildPacketFromUploads } from "@/lib/packet";
import { rupees, num, cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { Upload, FileText, AlertTriangle, X, CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import type { ClaimPacket, ExtractionResult } from "@/lib/types";

const ACCEPT_TYPES = {
  "application/pdf": [".pdf"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
  "image/webp": [".webp"],
  "image/tiff": [".tiff", ".tif"],
  "image/bmp": [".bmp"],
};

const BATCH_CAP = 500;

interface FileEntry {
  id: string;
  file: File;
  extraction?: ExtractionResult;
  packet?: ClaimPacket;
  error?: string;
}

function grossOf(packet: ClaimPacket): number {
  return packet.line_items.reduce((s, it) => s + num(it.amount), 0);
}

export function BatchCreate() {
  const router = useRouter();
  const { toast } = useToast();
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const withPacket = entries.filter((e) => e.packet);
  const dupIds = new Set(
    withPacket
      .map((e) => e.packet!.claim_id)
      .filter((id, i, arr) => arr.indexOf(id) !== i)
  );
  const ready = withPacket.filter((e) => !dupIds.has(e.packet!.claim_id));
  const dupRows = withPacket.length - ready.length;
  const failed = entries.filter((e) => e.error);
  const pending = entries.filter((e) => !e.packet && !e.error);
  const nearCap = ready.length > BATCH_CAP * 0.8;

  const onDrop = async (accepted: File[]) => {
    if (!accepted.length || extracting) return;
    const next: FileEntry[] = accepted.map((file, i) => ({
      id: `${Date.now()}-${i}`,
      file,
    }));
    setEntries((prev) => [...prev, ...next]);
    setExtracting(true);

    for (const entry of next) {
      try {
        const extraction = await claimiqApi.extract(entry.file);
        const packet = buildPacketFromUploads([extraction]);
        setEntries((prev) =>
          prev.map((e) => (e.id === entry.id ? { ...e, extraction, packet } : e))
        );
      } catch (err) {
        setEntries((prev) =>
          prev.map((e) =>
            e.id === entry.id ? { ...e, error: (err as Error).message } : e
          )
        );
      }
    }

    setExtracting(false);
  };

  const removeEntry = (id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id));
  };

  const handleSubmit = async () => {
    if (!ready.length || submitting) return;
    setSubmitting(true);
    try {
      const job = await claimiqApi.submitBatch(ready.map((e) => e.packet!));
      router.push(`/batches/${job.job_id}`);
    } catch (err) {
      toast(`Submit failed: ${(err as Error).message}`, "error");
      setSubmitting(false);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPT_TYPES,
    multiple: true,
    disabled: extracting || submitting,
  });

  return (
    <div className="card space-y-4">
      <div className="card-head">
        <h2 className="text-sm font-semibold">Submit a batch</h2>
        <span className="text-xs text-muted">
          Upload each bill separately — every file becomes one claim.
        </span>
      </div>

      {/* Dropzone */}
      <div
        {...getRootProps()}
        className={cn(
          "cursor-pointer border-2 border-dashed rounded-md transition-colors",
          isDragActive
            ? "border-accent bg-accent/5"
            : "border-line-2 hover:border-accent/50",
          (extracting || submitting) && "opacity-50 cursor-not-allowed"
        )}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <div className="w-10 h-10 rounded-full bg-panel-3 flex items-center justify-center mb-3">
            {isDragActive ? (
              <FileText size={20} className="text-accent" />
            ) : (
              <Upload size={20} className="text-muted" />
            )}
          </div>
          <p className="text-sm font-semibold text-white mb-1">
            {isDragActive
              ? "Drop bills here"
              : "Drop several bills here to audit them together"}
          </p>
          <p className="text-xs text-muted">
            PDF, PNG, JPG, WEBP, TIFF, BMP · one claim per file
          </p>
        </div>
      </div>

      {/* Extracting indicator */}
      {extracting && (
        <div className="flex items-center gap-3 text-sm text-muted">
          <Spinner />
          Reading documents...
        </div>
      )}

      {/* Cap note */}
      {nearCap && ready.length > 0 && (
        <div className="flex items-start gap-2 p-3 bg-amber-500/5 border border-amber-500/20 rounded text-sm text-amber-400">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {ready.length} claims ready — a batch may carry at most {BATCH_CAP}.
        </div>
      )}

      {/* Review table */}
      {entries.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-muted-2 text-left text-xs">
                <th className="py-2 px-3">File</th>
                <th className="py-2 px-3 text-right">Line items</th>
                <th className="py-2 px-3 text-right">Gross</th>
                <th className="py-2 px-3">Status</th>
                <th className="py-2 px-3 w-10" />
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b border-line/50">
                  <td className="py-2 px-3 max-w-[260px] truncate">{e.file.name}</td>
                  <td className="py-2 px-3 text-right font-mono text-xs">
                    {e.packet ? e.packet.line_items.length : e.error ? "—" : "…"}
                  </td>
                  <td className="py-2 px-3 text-right font-mono text-xs">
                    {e.packet && e.packet.line_items.length > 0 ? (
                      rupees(grossOf(e.packet))
                    ) : e.extraction?.document_label ? (
                      <span className="text-muted">{e.extraction.document_label}</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 px-3">
                    {e.error ? (
                      <div className="flex items-start gap-1.5 text-xs text-red-400">
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                        <span className="max-w-[320px] truncate">{e.error}</span>
                      </div>
                    ) : e.packet && dupIds.has(e.packet.claim_id) ? (
                      <div className="flex items-start gap-1.5 text-xs text-red-400">
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                        <span className="max-w-[320px] truncate">
                          duplicate claim id — same file name as another row
                        </span>
                      </div>
                    ) : e.packet ? (
                      <Badge variant="success">
                        <CheckCircle2 size={11} className="mr-1" />
                        ready
                      </Badge>
                    ) : (
                      <span className="flex items-center gap-1.5 text-xs text-muted">
                        <Loader2 size={12} className="animate-spin" />
                        reading
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3">
                    <button
                      onClick={() => removeEntry(e.id)}
                      disabled={extracting}
                      className="p-1 text-muted-2 hover:text-white disabled:opacity-40"
                      aria-label={`Remove ${e.file.name}`}
                    >
                      <X size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex items-center gap-4 pt-4">
            <Button
              variant="primary"
              disabled={!ready.length || extracting || submitting}
              onClick={handleSubmit}
            >
              {submitting ? <Loader2 size={14} className="animate-spin" /> : null}
              Submit batch
            </Button>
            <span className="text-xs text-muted">
              {ready.length} ready · {failed.length} failed
              {dupRows > 0 ? ` · ${dupRows} duplicate` : ""}
              {pending.length > 0 ? ` · ${pending.length} reading` : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

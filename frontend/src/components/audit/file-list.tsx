"use client";

import { FileText, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ExtractionResult } from "@/lib/types";

interface FileListProps {
  uploads: ExtractionResult[];
}

export function FileList({ uploads }: FileListProps) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold mb-3">
        Uploaded documents ({uploads.length})
      </h3>
      <div className="space-y-2">
        {uploads.map((u, i) => (
          <div
            key={i}
            className="flex items-center gap-3 p-3 bg-panel-3 rounded-md"
          >
            <FileText size={16} className="text-muted shrink-0" />
            <span className="text-sm font-medium truncate">{u.filename}</span>
            <Badge variant="default">{u.file_kind}</Badge>
            <span className="text-xs text-muted ml-auto">
              {u.line_items.length > 0
                ? `${u.line_items.length} line items · ${u.how}`
                : u.document_label}
            </span>
            {u.low_confidence > 0 && (
              <Badge variant="warning">
                <AlertTriangle size={10} className="mr-1" />
                {u.low_confidence} low confidence
              </Badge>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

"use client";

import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

const ACCEPT_TYPES = {
  "application/pdf": [".pdf"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
  "image/webp": [".webp"],
  "image/tiff": [".tiff", ".tif"],
  "image/bmp": [".bmp"],
};

interface UploadZoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}

export function UploadZone({ onFiles, disabled }: UploadZoneProps) {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      if (!disabled && acceptedFiles.length > 0) {
        onFiles(acceptedFiles);
      }
    },
    [onFiles, disabled]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPT_TYPES,
    multiple: true,
    disabled,
  });

  return (
    <div
      {...getRootProps()}
      className={cn(
        "card cursor-pointer border-2 border-dashed transition-colors",
        isDragActive
          ? "border-accent bg-accent/5"
          : "border-line-2 hover:border-accent/50",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <input {...getInputProps()} />
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <div className="w-12 h-12 rounded-full bg-panel-3 flex items-center justify-center mb-4">
          {isDragActive ? (
            <FileText size={24} className="text-accent" />
          ) : (
            <Upload size={24} className="text-muted" />
          )}
        </div>
        <h3 className="font-semibold text-white mb-1">
          {isDragActive ? "Drop files here" : "Drop the claim packet here"}
        </h3>
        <p className="text-sm text-muted max-w-md">
          Bill, discharge summary, claim form, policy schedule, reports. Native
          PDFs are parsed exactly; scans and photos are read with on-device OCR.
        </p>
        <p className="text-xs text-muted-2 mt-2">
          PDF, PNG, JPG, WEBP, TIFF, BMP
        </p>
      </div>
    </div>
  );
}

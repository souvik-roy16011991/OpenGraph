"use client";

import * as React from "react";
import { Upload, FileJson, CheckCircle2, X, AlertCircle } from "lucide-react";
import { cn, formatBytes } from "@/lib/utils";

export interface KbFilePreview {
  file: File;
  title?: string;
  subtitle?: string;
  chapters: number;
  ok: boolean;
  error?: string;
}

async function parsePreview(file: File): Promise<KbFilePreview> {
  try {
    const text = await file.text();
    const json = JSON.parse(text);
    if (!json || typeof json !== "object" || !Array.isArray((json as { chapters: unknown }).chapters)) {
      return { file, chapters: 0, ok: false, error: "Missing top-level `chapters: [...]` array" };
    }
    return {
      file,
      title: (json as { title?: string }).title,
      subtitle: (json as { subtitle?: string }).subtitle,
      chapters: (json as { chapters: unknown[] }).chapters.length,
      ok: true,
    };
  } catch (exc) {
    return { file, chapters: 0, ok: false, error: (exc as Error).message };
  }
}

export function KbDropzone({
  label,
  helpText,
  value,
  onChange,
  accentColor = "from-sky-500/20 to-indigo-500/20",
}: {
  label: string;
  helpText: string;
  value: KbFilePreview | null;
  onChange: (v: KbFilePreview | null) => void;
  accentColor?: string;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [hover, setHover] = React.useState(false);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const preview = await parsePreview(files[0]);
    onChange(preview);
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">{label}</span>
        {value?.ok && (
          <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => onChange(null)}>
            Remove
          </button>
        )}
      </div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setHover(true);
        }}
        onDragLeave={() => setHover(false)}
        onDrop={(e) => {
          e.preventDefault();
          setHover(false);
          void handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "relative rounded-xl border-2 border-dashed bg-gradient-to-br transition-all cursor-pointer",
          "flex flex-col items-center justify-center p-8 min-h-[220px]",
          hover ? "border-primary" : "border-border hover:border-foreground/30",
          value ? "bg-card" : accentColor
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        {!value && (
          <>
            <Upload className="h-8 w-8 text-muted-foreground mb-3" />
            <p className="text-sm font-medium">Drop a JSON file or click to browse</p>
            <p className="text-xs text-muted-foreground mt-1">{helpText}</p>
          </>
        )}
        {value && (
          <div className="w-full flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <FileJson className="h-8 w-8 shrink-0 text-primary" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{value.file.name}</p>
                <p className="text-xs text-muted-foreground">{formatBytes(value.file.size)}</p>
              </div>
              {value.ok ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0" />
              ) : (
                <AlertCircle className="h-5 w-5 text-destructive shrink-0" />
              )}
            </div>
            {value.ok ? (
              <div className="rounded-md bg-background/60 p-3 space-y-0.5 text-xs">
                {value.title && <p className="font-medium">{value.title}</p>}
                {value.subtitle && <p className="text-muted-foreground">{value.subtitle}</p>}
                <p className="font-mono text-muted-foreground">{value.chapters} chapters detected</p>
              </div>
            ) : (
              <div className="rounded-md bg-destructive/10 p-3 text-xs text-destructive">
                <span className="font-medium">Invalid: </span>{value.error}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

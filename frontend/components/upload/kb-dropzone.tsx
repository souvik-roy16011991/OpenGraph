"use client";

import * as React from "react";
import { Upload, FileText, CheckCircle2, AlertCircle, X, Plus } from "lucide-react";

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
      return { file, chapters: 0, ok: false, error: "Invalid format — missing data array" };

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

/**
 * Multi-file dropzone. Accepts N JSON files, validates each in-browser,
 * shows per-file status, and emits the full list via onChange.
 */
export function KbDropzone({
  label,
  helpText,
  value,
  onChange,
  accentColor = "bg-gradient-to-br from-sky-500/10 to-indigo-500/10",
}: {
  label: string;
  helpText: string;
  value: KbFilePreview[];
  onChange: (v: KbFilePreview[]) => void;
  accentColor?: string;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [hover, setHover] = React.useState(false);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const previews = await Promise.all(Array.from(files).map((f) => parsePreview(f)));
    // Dedup by name+size against existing list
    const existingKeys = new Set(value.map((v) => `${v.file.name}:${v.file.size}`));
    const fresh = previews.filter((p) => !existingKeys.has(`${p.file.name}:${p.file.size}`));
    onChange([...value, ...fresh]);
  }

  function removeAt(idx: number) {
    onChange(value.filter((_, i) => i !== idx));
  }

  const validCount = value.filter((v) => v.ok).length;
  const invalidCount = value.length - validCount;
  const totalChapters = value.filter((v) => v.ok).reduce((sum, v) => sum + v.chapters, 0);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">{label}</span>
        {value.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {validCount} valid{invalidCount ? ` · ${invalidCount} rejected` : ""}
            {totalChapters ? ` · ${totalChapters} chapters` : ""}
          </span>
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
          "relative rounded-xl border-2 border-dashed cursor-pointer transition-all p-5 min-h-[140px]",
          "flex flex-col items-center justify-center gap-2",
          hover ? "border-primary" : "border-border hover:border-foreground/30",
          accentColor
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/json,.json"
          multiple
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        {value.length === 0 ? (
          <>
            <Upload className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm font-medium">Drop files or click to browse</p>

            <p className="text-xs text-muted-foreground">{helpText} · multi-select supported</p>
          </>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Plus className="h-4 w-4" />
            <span>Add more files</span>

          </div>
        )}
      </div>

      {value.length > 0 && (
        <ul className="space-y-1.5">
          {value.map((v, i) => (
            <li
              key={i}
              className={cn(
                "flex items-center gap-3 rounded-md border px-3 py-2 bg-card text-sm",
                !v.ok && "border-destructive/50 bg-destructive/5"
              )}
            >
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="truncate font-medium">{v.file.name}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {formatBytes(v.file.size)}
                  {v.ok
                    ? ` · ${v.chapters} chapters${v.title ? ` · ${v.title}` : ""}`
                    : ` · ${v.error}`}
                </p>
              </div>
              {v.ok ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
              ) : (
                <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
              )}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeAt(i);
                }}
                className="text-muted-foreground hover:text-destructive transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Lang = "curl" | "python" | "typescript" | "node";

export function CodeSnippet({
  code,
  lang,
  className,
}: {
  code: string;
  lang: Lang;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // No-op — the <pre> is selectable anyway.
    }
  }

  return (
    <div className={cn("relative group rounded-md border bg-[#0b1020] text-slate-100", className)}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/10">
        <span className="text-[10px] uppercase tracking-wider font-medium text-slate-400">
          {lang === "typescript" ? "TypeScript" : lang === "node" ? "Node" : lang}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onCopy}
          className="h-7 gap-1.5 text-slate-300 hover:bg-white/10 hover:text-white"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-emerald-400" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </Button>
      </div>
      <pre className="px-4 py-3 text-xs leading-relaxed overflow-x-auto whitespace-pre font-mono">
        <code>{code}</code>
      </pre>
    </div>
  );
}

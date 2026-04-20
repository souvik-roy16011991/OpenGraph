"use client";

import * as React from "react";
import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LLMModel } from "@/lib/schema";

/**
 * Searchable, grouped model picker. Built on Radix Popover + a plain input
 * filter (342 OpenRouter models is too many for a native <select> and Radix
 * Select has no built-in search).
 *
 * Models are grouped by provider prefix (anthropic/, openai/, …) so users can
 * scan by vendor. Pricing — when present — is shown as per-million-token USD
 * for quick cost comparison.
 */
export interface ModelSelectProps {
  models: LLMModel[];
  value: string | null;            // currently-selected model id
  onChange: (id: string) => void;  // fired on selection (no clear — dropdown always picks one)
  defaultModel?: string;           // env LLM_MODEL (shown as "default" badge on that row)
  loading?: boolean;
  disabled?: boolean;
  className?: string;
}

function groupByProvider(models: LLMModel[]): Array<[string, LLMModel[]]> {
  const buckets = new Map<string, LLMModel[]>();
  for (const m of models) {
    const provider = m.id.includes("/") ? m.id.split("/")[0] : "other";
    if (!buckets.has(provider)) buckets.set(provider, []);
    buckets.get(provider)!.push(m);
  }
  // Alphabetical provider order, alphabetical models within
  return Array.from(buckets.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([prov, list]) => [prov, [...list].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id))]);
}

function formatPricePerMillion(raw: string | number | null | undefined): string | null {
  if (raw === null || raw === undefined) return null;
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (!Number.isFinite(n) || n <= 0) return null;
  // OpenRouter publishes per-token USD. Multiply for the more human "per 1M" figure.
  const perMillion = n * 1_000_000;
  if (perMillion >= 1) return `$${perMillion.toFixed(2)}`;
  return `$${perMillion.toFixed(3)}`;
}

export function ModelSelect({
  models,
  value,
  onChange,
  defaultModel,
  loading,
  disabled,
  className,
}: ModelSelectProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => {
      const name = (m.name || "").toLowerCase();
      const id = m.id.toLowerCase();
      return id.includes(q) || name.includes(q);
    });
  }, [models, query]);

  const groups = React.useMemo(() => groupByProvider(filtered), [filtered]);

  const selected = React.useMemo(
    () => models.find((m) => m.id === value) ?? null,
    [models, value]
  );

  // Focus the search on open; a searchable dropdown that ignores typing on
  // open is uniformly infuriating.
  React.useEffect(() => {
    if (open) {
      setQuery("");
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const triggerLabel = loading
    ? "Loading models…"
    : selected
    ? selected.name || selected.id
    : value || "Select model…";

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          disabled={disabled || loading}
          className={cn(
            "inline-flex items-center justify-between gap-2 rounded-md border border-input bg-background px-3 h-9 text-sm min-w-[220px] max-w-[320px]",
            "hover:bg-accent hover:text-accent-foreground disabled:opacity-50 disabled:pointer-events-none",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className
          )}
          aria-label="Select LLM model"
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className={cn(
            "z-50 w-[420px] max-w-[calc(100vw-1rem)] rounded-md border bg-popover text-popover-foreground shadow-md outline-none",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
          )}
        >
          <div className="flex items-center gap-2 border-b px-3 h-10">
            <Search className="h-3.5 w-3.5 opacity-60" />
            <input
              ref={inputRef}
              type="text"
              placeholder="Search models…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            {defaultModel ? (
              <span className="text-[10px] text-muted-foreground font-mono">default: {defaultModel}</span>
            ) : null}
          </div>

          <div className="max-h-[360px] overflow-y-auto py-1">
            {groups.length === 0 ? (
              <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                {loading ? "Loading…" : "No models match."}
              </div>
            ) : (
              groups.map(([provider, list]) => (
                <div key={provider} className="py-1">
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                    {provider}
                  </div>
                  {list.map((m) => {
                    const isSelected = m.id === value;
                    const isDefault = defaultModel === m.id;
                    const prompt = formatPricePerMillion(m.pricing?.prompt);
                    const completion = formatPricePerMillion(m.pricing?.completion);
                    return (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => {
                          onChange(m.id);
                          setOpen(false);
                        }}
                        className={cn(
                          "w-full px-3 py-1.5 flex items-start gap-2 text-left text-sm",
                          "hover:bg-accent hover:text-accent-foreground",
                          isSelected && "bg-accent/60"
                        )}
                      >
                        <Check
                          className={cn(
                            "h-3.5 w-3.5 mt-0.5 shrink-0",
                            isSelected ? "opacity-100" : "opacity-0"
                          )}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="truncate font-medium">{m.name || m.id}</span>
                            {isDefault && (
                              <span className="text-[9px] uppercase tracking-wider text-muted-foreground font-mono">
                                default
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-muted-foreground truncate font-mono">{m.id}</div>
                        </div>
                        {(prompt || completion) && (
                          <div className="text-right text-[10px] text-muted-foreground whitespace-nowrap font-mono">
                            {prompt ?? "—"} / {completion ?? "—"}
                            <div className="text-[9px] uppercase tracking-wider opacity-60">per 1M</div>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

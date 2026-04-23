"use client";

import * as React from "react";
import * as Icons from "lucide-react";
import { ArrowRight, Loader2, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { KBTemplate } from "@/lib/schema";

interface Props {
  template: KBTemplate;
  onUse: (t: KBTemplate) => void;
  onEdit?: (t: KBTemplate) => void;
  onDelete?: (t: KBTemplate) => void;
  busy?: boolean;
}

function IconFor({ name }: { name: string }) {
  const Icon =
    (Icons as unknown as Record<string, React.ComponentType<{ className?: string }>>)[name]
    ?? Icons.Folder;
  return <Icon className="h-5 w-5 text-foreground/70" />;
}

/**
 * OpenRouter-style search result row.
 *
 * Dense horizontal layout — icon, title + slug, description, meta chips,
 * action button. The whole row is clickable (primary action = instantiate);
 * hover reveals edit / delete icons for custom templates. Intended to be
 * stacked vertically as ``space-y-2`` inside the templates page.
 */
export function TemplateCard({ template, onUse, onEdit, onDelete, busy }: Props) {
  const isCustom = template.source === "custom";
  const canEdit = template.editable && Boolean(onEdit);
  const canDelete = template.editable && Boolean(onDelete);
  const identifier = template.slug || `custom · ${template.domain.domain_name || "—"}`;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Use template ${template.name}`}
      onClick={() => !busy && onUse(template)}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !busy) {
          e.preventDefault();
          onUse(template);
        }
      }}
      className={cn(
        "group relative flex items-center gap-4 rounded-lg border bg-card px-4 py-3",
        "transition-all duration-150 cursor-pointer",
        "hover:border-foreground/25 hover:bg-accent/30 hover:shadow-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        busy && "opacity-60 pointer-events-none",
      )}
    >
      <div className="h-10 w-10 rounded-md border bg-background flex items-center justify-center shrink-0">
        <IconFor name={template.icon} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="font-medium text-sm leading-tight truncate">{template.name}</h3>
          <span className="text-[10px] font-mono text-muted-foreground/80 truncate">
            {identifier}
          </span>
          {isCustom && (
            <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
              yours
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
          {template.description}
        </p>
      </div>

      <div className="hidden md:flex items-center gap-1.5 shrink-0">
        <Badge variant="outline" className="text-[10px]">
          {template.category}
        </Badge>
      </div>

      <div className="flex items-center gap-1 shrink-0">
        {(canEdit || canDelete) && (
          <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            {canEdit && (
              <Tooltip delayDuration={200}>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={(e) => {
                      e.stopPropagation();
                      onEdit?.(template);
                    }}
                    aria-label={`Edit ${template.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Edit</TooltipContent>
              </Tooltip>
            )}
            {canDelete && (
              <Tooltip delayDuration={200}>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 hover:text-destructive"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete?.(template);
                    }}
                    aria-label={`Delete ${template.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Delete</TooltipContent>
              </Tooltip>
            )}
          </div>
        )}
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 h-8"
          onClick={(e) => {
            e.stopPropagation();
            onUse(template);
          }}
          disabled={busy}
        >
          {busy ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Creating…
            </>
          ) : (
            <>
              Use
              <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

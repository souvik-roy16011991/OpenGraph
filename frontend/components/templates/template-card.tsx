"use client";

import * as React from "react";
import * as Icons from "lucide-react";
import { Pencil, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { KBTemplate } from "@/lib/schema";

interface Props {
  template: KBTemplate;
  onUse: (t: KBTemplate) => void;
  onEdit?: (t: KBTemplate) => void;
  onDelete?: (t: KBTemplate) => void;
  busy?: boolean;
}

function IconFor({ name }: { name: string }) {
  // Look up the lucide icon by name. Fall back to a generic folder.
  const Icon =
    (Icons as unknown as Record<string, React.ComponentType<{ className?: string }>>)[name]
    ?? Icons.Folder;
  return <Icon className="h-5 w-5 text-muted-foreground" />;
}

export function TemplateCard({ template, onUse, onEdit, onDelete, busy }: Props) {
  const isCustom = template.source === "custom";
  const canEdit = template.editable && Boolean(onEdit);
  const canDelete = template.editable && Boolean(onDelete);

  return (
    <Card className="flex flex-col">
      <CardContent className="p-4 flex-1 flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <div className="h-9 w-9 rounded-md border bg-background/60 flex items-center justify-center shrink-0">
            <IconFor name={template.icon} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-medium leading-tight truncate">{template.name}</h3>
            {template.slug ? (
              <p className="text-[11px] font-mono text-muted-foreground truncate">
                {template.slug}
              </p>
            ) : (
              <p className="text-[11px] font-mono text-muted-foreground truncate">
                custom · {template.domain.domain_name || "—"}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isCustom && (
              <Badge variant="secondary" className="text-[10px]">
                yours
              </Badge>
            )}
            <Badge variant="outline" className="text-[10px]">
              {template.category}
            </Badge>
          </div>
        </div>

        <p className="text-sm text-muted-foreground line-clamp-3 flex-1">
          {template.description}
        </p>

        <div className="flex items-center justify-between gap-2">
          <Button
            size="sm"
            onClick={() => onUse(template)}
            disabled={busy}
          >
            {busy ? "Creating…" : "Use this template"}
          </Button>

          {(canEdit || canDelete) && (
            <div className="flex items-center gap-1">
              {canEdit && (
                <Tooltip delayDuration={200}>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => onEdit?.(template)}
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
                      className="h-8 w-8 hover:text-destructive"
                      onClick={() => onDelete?.(template)}
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
        </div>
      </CardContent>
    </Card>
  );
}

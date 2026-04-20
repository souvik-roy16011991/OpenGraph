"use client";

import * as React from "react";
import * as Icons from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { KBTemplate } from "@/lib/schema";

interface Props {
  template: KBTemplate;
  onUse: (t: KBTemplate) => void;
  busy?: boolean;
}

function IconFor({ name }: { name: string }) {
  // Look up the lucide icon by name. Fall back to a generic folder.
  const Icon = (Icons as unknown as Record<string, React.ComponentType<{ className?: string }>>)[name]
    ?? Icons.Folder;
  return <Icon className="h-5 w-5 text-muted-foreground" />;
}

export function TemplateCard({ template, onUse, busy }: Props) {
  return (
    <Card className="flex flex-col">
      <CardContent className="p-4 flex-1 flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <div className="h-9 w-9 rounded-md border bg-background/60 flex items-center justify-center shrink-0">
            <IconFor name={template.icon} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-medium leading-tight truncate">{template.name}</h3>
            <p className="text-[11px] font-mono text-muted-foreground truncate">{template.slug}</p>
          </div>
          <Badge variant="outline" className="text-[10px] shrink-0">
            {template.category}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground line-clamp-3 flex-1">
          {template.description}
        </p>
        <Button
          size="sm"
          onClick={() => onUse(template)}
          disabled={busy}
          className="self-start"
        >
          {busy ? "Creating…" : "Use this template"}
        </Button>
      </CardContent>
    </Card>
  );
}

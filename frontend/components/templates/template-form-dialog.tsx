"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { KBTemplate, TemplateCreateInput } from "@/lib/schema";

/**
 * Shared create / edit dialog for user-custom templates.
 *
 * Opens controlled (parent passes ``open`` + ``onOpenChange``). If
 * ``initial`` is passed the dialog starts in edit-mode with the values
 * pre-filled; if absent it's a blank create form.
 *
 * Submit hands the payload up as ``onSubmit`` — the parent owns the
 * mutation + loading/error state so this component stays dumb.
 */
export interface TemplateFormDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  initial?: KBTemplate | null;
  onSubmit: (values: TemplateCreateInput) => Promise<void>;
  submitError?: string | null;
  submitting?: boolean;
}

const BLANK: TemplateCreateInput = {
  name: "",
  description: "",
  category: "custom",
  icon: "BookOpen",
  domain: {
    domain_name: "",
    domain_display_name: "",
    organization_name: "",
    knowledge_focus_examples: "",
    tool_focus_examples: "",
  },
};

function toFormValues(t: KBTemplate | null | undefined): TemplateCreateInput {
  if (!t) return { ...BLANK, domain: { ...BLANK.domain } };
  return {
    name: t.name,
    description: t.description,
    category: t.category,
    icon: t.icon,
    domain: { ...t.domain },
  };
}

export function TemplateFormDialog({
  open,
  onOpenChange,
  initial,
  onSubmit,
  submitError,
  submitting,
}: TemplateFormDialogProps) {
  const [values, setValues] = React.useState<TemplateCreateInput>(() =>
    toFormValues(initial),
  );
  const [localError, setLocalError] = React.useState<string | null>(null);

  // Reset the form whenever the dialog opens (or the target template changes).
  React.useEffect(() => {
    if (open) {
      setValues(toFormValues(initial));
      setLocalError(null);
    }
  }, [open, initial?.id]);

  const isEdit = Boolean(initial);

  function update<K extends keyof TemplateCreateInput>(
    key: K,
    v: TemplateCreateInput[K],
  ) {
    setValues((prev) => ({ ...prev, [key]: v }));
  }
  function updateDomain<K extends keyof TemplateCreateInput["domain"]>(
    key: K,
    v: string,
  ) {
    setValues((prev) => ({ ...prev, domain: { ...prev.domain, [key]: v } }));
  }

  function validate(): string | null {
    if (!values.name.trim()) return "Name is required.";
    if (!values.description.trim()) return "Description is required.";
    if (!values.category.trim()) return "Category is required.";
    if (!values.icon.trim()) return "Icon name is required.";
    const d = values.domain;
    if (!d.domain_name.trim()) return "Domain name is required.";
    if (!d.domain_display_name.trim()) return "Domain display name is required.";
    if (!d.organization_name.trim()) return "Organization name is required.";
    if (!d.knowledge_focus_examples.trim())
      return "Knowledge focus examples are required.";
    if (!d.tool_focus_examples.trim())
      return "Tool focus examples are required.";
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLocalError(null);
    const err = validate();
    if (err) {
      setLocalError(err);
      return;
    }
    await onSubmit(values);
  }

  const errorText = localError ?? submitError ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit template" : "Create template"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update this template. Workspaces you've already created from it are unaffected."
              : "Build a reusable template. Instantiating it creates a new workspace pre-seeded with the domain profile below."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          {/* Identity */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="tpl-name">Name</Label>
              <Input
                id="tpl-name"
                value={values.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="Customer Onboarding"
                disabled={submitting}
                maxLength={128}
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="tpl-desc">Description</Label>
              <Textarea
                id="tpl-desc"
                value={values.description}
                onChange={(e) => update("description", e.target.value)}
                placeholder="Short summary shown on the template card."
                disabled={submitting}
                rows={2}
                maxLength={2000}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tpl-cat">Category</Label>
              <Input
                id="tpl-cat"
                value={values.category}
                onChange={(e) => update("category", e.target.value)}
                placeholder="custom"
                disabled={submitting}
                maxLength={64}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tpl-icon">
                Icon <span className="text-muted-foreground text-xs">(lucide name)</span>
              </Label>
              <Input
                id="tpl-icon"
                value={values.icon}
                onChange={(e) => update("icon", e.target.value)}
                placeholder="BookOpen"
                disabled={submitting}
                maxLength={32}
              />
            </div>
          </div>

          {/* Domain profile */}
          <div className="pt-2 border-t space-y-3">
            <div>
              <h3 className="text-sm font-semibold">Domain profile</h3>
              <p className="text-xs text-muted-foreground">
                These five fields are copied onto every workspace instantiated
                from this template.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="d-name">domain_name</Label>
                <Input
                  id="d-name"
                  value={values.domain.domain_name}
                  onChange={(e) => updateDomain("domain_name", e.target.value)}
                  placeholder="customer_onboarding"
                  disabled={submitting}
                  maxLength={128}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="d-display">domain_display_name</Label>
                <Input
                  id="d-display"
                  value={values.domain.domain_display_name}
                  onChange={(e) =>
                    updateDomain("domain_display_name", e.target.value)
                  }
                  placeholder="Customer Onboarding"
                  disabled={submitting}
                  maxLength={128}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="d-org">organization_name</Label>
                <Input
                  id="d-org"
                  value={values.domain.organization_name}
                  onChange={(e) =>
                    updateDomain("organization_name", e.target.value)
                  }
                  placeholder="Acme Bank — Retail Ops"
                  disabled={submitting}
                  maxLength={128}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="d-know">knowledge_focus_examples</Label>
                <Textarea
                  id="d-know"
                  value={values.domain.knowledge_focus_examples}
                  onChange={(e) =>
                    updateDomain("knowledge_focus_examples", e.target.value)
                  }
                  placeholder="KYC policies, AML flags, account opening SOPs, ..."
                  disabled={submitting}
                  rows={2}
                  maxLength={4000}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="d-tool">tool_focus_examples</Label>
                <Textarea
                  id="d-tool"
                  value={values.domain.tool_focus_examples}
                  onChange={(e) =>
                    updateDomain("tool_focus_examples", e.target.value)
                  }
                  placeholder="CRM, KYC vendor APIs, document capture, ..."
                  disabled={submitting}
                  rows={2}
                  maxLength={4000}
                />
              </div>
            </div>
          </div>

          {errorText && (
            <p className="text-xs text-destructive" role="alert">
              {errorText}
            </p>
          )}

          <div className="flex items-center justify-end gap-2 pt-2 border-t">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              {isEdit ? "Save changes" : "Create template"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

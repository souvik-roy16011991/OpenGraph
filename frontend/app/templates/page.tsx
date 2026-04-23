"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { TemplateCard } from "@/components/templates/template-card";
import { TemplateFormDialog } from "@/components/templates/template-form-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkspaceStore } from "@/store/workspace-store";
import type {
  KBTemplate,
  TemplateCreateInput,
  TemplateUpdateInput,
} from "@/lib/schema";

/**
 * KB template catalog — stock + user-owned custom templates, with full
 * create / edit / delete on the custom ones.
 *
 * - Clicking "Use this template" on any row hits
 *   POST /api/v1/templates/{id}/instantiate which creates a new workspace
 *   pre-populated with the template's domain profile. We set it active and
 *   route to /upload.
 * - The "+ Create template" button opens a dialog that POSTs a new custom
 *   template; the form dialog is reused in edit mode for owned rows.
 * - Delete uses a confirmation dialog; stock templates never show edit/delete.
 */

export default function TemplatesPage() {
  const [query, setQuery] = React.useState("");
  const [category, setCategory] = React.useState<string | null>(null);
  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<KBTemplate | null>(null);
  const [toDelete, setToDelete] = React.useState<KBTemplate | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);
  const router = useRouter();
  const qc = useQueryClient();
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);

  const templatesQuery = useQuery({
    queryKey: ["templates", query, category],
    queryFn: () =>
      api.listTemplates({
        q: query || undefined,
        category: category || undefined,
      }),
    staleTime: 60_000,
  });

  const all = templatesQuery.data?.templates ?? [];
  const categories = React.useMemo(
    () => Array.from(new Set(all.map((t) => t.category))).sort(),
    [all],
  );

  const instantiate = useMutation({
    mutationFn: (t: KBTemplate) => api.instantiateTemplate(t.id),
    onSuccess: (resp) => {
      setActiveId(resp.id);
      router.push("/upload");
    },
  });

  const createMutation = useMutation({
    mutationFn: (body: TemplateCreateInput) => api.createTemplate(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
    },
    onError: (e) => setFormError(errorMessage(e)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TemplateUpdateInput }) =>
      api.updateTemplate(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
    },
    onError: (e) => setFormError(errorMessage(e)),
  });

  const deleteMutation = useMutation({
    mutationFn: (t: KBTemplate) => api.deleteTemplate(t.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setToDelete(null);
    },
  });

  function onCreate() {
    setEditing(null);
    setFormError(null);
    setFormOpen(true);
  }
  function onEdit(t: KBTemplate) {
    setEditing(t);
    setFormError(null);
    setFormOpen(true);
  }
  async function onSubmitForm(values: TemplateCreateInput) {
    setFormError(null);
    if (editing) {
      await updateMutation.mutateAsync({ id: editing.id, body: values });
    } else {
      await createMutation.mutateAsync(values);
    }
  }

  const submitting = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Start from a template
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Pre-configured knowledge-base shapes. Pick one to create a workspace
            with its domain profile dialed in, or build your own from scratch.
          </p>
        </div>
        <Button onClick={onCreate} className="gap-2">
          <Plus className="h-4 w-4" /> Create template
        </Button>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search templates…"
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => setCategory(null)}
            className={`px-2.5 py-1 rounded-md text-xs border ${
              category === null ? "bg-accent border-accent" : "hover:bg-accent/50"
            }`}
          >
            all
          </button>
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`px-2.5 py-1 rounded-md text-xs border ${
                category === c ? "bg-accent border-accent" : "hover:bg-accent/50"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {instantiate.isError && (
        <div className="text-sm text-destructive">
          Couldn&apos;t create workspace — {errorMessage(instantiate.error)}
        </div>
      )}
      {deleteMutation.isError && (
        <div className="text-sm text-destructive">
          Delete failed — {errorMessage(deleteMutation.error)}
        </div>
      )}

      {templatesQuery.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Card key={i}>
              <CardHeader className="space-y-2">
                <Skeleton className="h-8 w-8 rounded-md" />
                <Skeleton className="h-5 w-3/4" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-4/5" />
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex gap-1.5">
                  <Skeleton className="h-5 w-16 rounded-full" />
                  <Skeleton className="h-5 w-20 rounded-full" />
                </div>
                <Skeleton className="h-9 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : all.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No templates match that search.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {all.map((t) => (
            <TemplateCard
              key={t.id}
              template={t}
              onUse={(tmpl) => instantiate.mutate(tmpl)}
              onEdit={t.editable ? onEdit : undefined}
              onDelete={t.editable ? (tmpl) => setToDelete(tmpl) : undefined}
              busy={instantiate.isPending && instantiate.variables?.id === t.id}
            />
          ))}
        </div>
      )}

      <div className="pt-4 border-t">
        <p className="text-xs text-muted-foreground">
          Already have workspaces?{" "}
          <a href="/workspaces" className="underline hover:text-foreground">
            Open an existing one
          </a>
          .
        </p>
      </div>

      <TemplateFormDialog
        open={formOpen}
        onOpenChange={(v) => {
          if (!v) {
            setFormOpen(false);
            setEditing(null);
            setFormError(null);
          } else {
            setFormOpen(true);
          }
        }}
        initial={editing}
        onSubmit={onSubmitForm}
        submitError={formError}
        submitting={submitting}
      />

      <Dialog
        open={Boolean(toDelete)}
        onOpenChange={(v) => !v && setToDelete(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete template?</DialogTitle>
            <DialogDescription>
              {toDelete ? (
                <>
                  This will permanently delete{" "}
                  <span className="font-medium text-foreground">
                    {toDelete.name}
                  </span>
                  . Workspaces you&apos;ve already created from it are
                  unaffected — this only removes the template itself.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => setToDelete(null)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => toDelete && deleteMutation.mutate(toDelete)}
              disabled={deleteMutation.isPending}
              className="gap-2"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              Delete
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { TemplateCard } from "@/components/templates/template-card";
import { useWorkspaceStore } from "@/store/workspace-store";
import type { KBTemplate } from "@/lib/schema";

/**
 * KB template catalog — OpenRouter-style landing.
 *
 * Fetches the (public) catalog, lets the user filter by category and search
 * substring, and on "Use this template" hits /api/v1/templates/{slug}/
 * instantiate which creates a new workspace pre-populated with the
 * template's domain. We then set it as the active workspace and route to
 * /upload so the user can drop in their files.
 */

export default function TemplatesPage() {
  const [query, setQuery] = React.useState("");
  const [category, setCategory] = React.useState<string | null>(null);
  const router = useRouter();
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
    mutationFn: (t: KBTemplate) => api.instantiateTemplate(t.slug),
    onSuccess: (resp) => {
      setActiveId(resp.id);
      router.push("/upload");
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Start from a template</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Pre-configured knowledge-base shapes. Pick one to create a workspace
          that already has its domain profile dialed in — you only need to
          drop in your knowledge and tool JSONs.
        </p>
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

      {templatesQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading catalog…</p>
      ) : all.length === 0 ? (
        <p className="text-sm text-muted-foreground">No templates match that search.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {all.map((t) => (
            <TemplateCard
              key={t.slug}
              template={t}
              onUse={(tmpl) => instantiate.mutate(tmpl)}
              busy={instantiate.isPending && instantiate.variables?.slug === t.slug}
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
    </div>
  );
}

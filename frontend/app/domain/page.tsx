"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Tag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { DomainPayloadSchema, type DomainPayload } from "@/lib/schema";
import { useRequireWorkspace } from "@/hooks/use-require-workspace";
import { useWizardStore } from "@/store/wizard-store";

export default function DomainPage() {
  const router = useRouter();
  const activeWs = useRequireWorkspace();
  const markCompleted = useWizardStore((s) => s.markCompleted);

  const query = useQuery({
    queryKey: ["domain", activeWs],
    queryFn: api.getDomain,
    enabled: Boolean(activeWs),
  });

  const form = useForm<DomainPayload>({
    resolver: zodResolver(DomainPayloadSchema),
    defaultValues: query.data ?? {
      domain_name: "",
      domain_display_name: "",
      organization_name: "",
      knowledge_focus_examples: "",
      tool_focus_examples: "",
    },
    values: query.data,
  });

  const mutation = useMutation({
    mutationFn: (p: DomainPayload) => api.putDomain(p),
    onSuccess: () => {
      toast.success("Domain saved");
      markCompleted("domain", true);
      setTimeout(() => router.push("/graph-config"), 300);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Domain profile</h1>
          <p className="text-muted-foreground text-sm">
            Context for the agent's prompts and the UI. Maps to <code className="font-mono text-xs">kb-config/domain.yaml</code>.
          </p>
        </div>
      </div>

      <form onSubmit={form.handleSubmit((v) => mutation.mutate(v))} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Tag className="h-5 w-5" /> Identity</CardTitle>
            <CardDescription>Names used in IDs, UI headers, and agent prompts.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <Field label="Domain name (slug)" hint="Short machine identifier, e.g. `loan_assessment`">
              <Input placeholder="loan_assessment" {...form.register("domain_name")} />
              <FieldError name="domain_name" errors={form.formState.errors} />
            </Field>
            <Field label="Display name" hint="Shown in the UI and agent responses">
              <Input placeholder="Loan Application Assessment" {...form.register("domain_display_name")} />
              <FieldError name="domain_display_name" errors={form.formState.errors} />
            </Field>
            <Field label="Organization" hint="Who owns this domain — used in prompts" className="md:col-span-2">
              <Input placeholder="the Credit Underwriting Desk" {...form.register("organization_name")} />
              <FieldError name="organization_name" errors={form.formState.errors} />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Focus examples</CardTitle>
            <CardDescription>
              Comma-separated themes that help the LLM frame responses. Concrete and specific beats generic.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Field
              label="Knowledge focus examples"
              hint="Topics covered by the knowledge KB (policies, rules, concepts)"
            >
              <Textarea
                rows={3}
                placeholder="applicant classification, income eligibility, FOIR, bureau score cut-offs, documentation rules, risk tiering"
                {...form.register("knowledge_focus_examples")}
              />
              <FieldError name="knowledge_focus_examples" errors={form.formState.errors} />
            </Field>
            <Field
              label="Tool focus examples"
              hint="Systems / integrations covered by the tool KB"
            >
              <Textarea
                rows={3}
                placeholder="credit bureaus (CIBIL, Experian), KYC platforms, bank statement analyzers, loan origination systems"
                {...form.register("tool_focus_examples")}
              />
              <FieldError name="tool_focus_examples" errors={form.formState.errors} />
            </Field>
          </CardContent>
        </Card>

        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            {query.isLoading ? "Loading current values…" : "Saving writes to domain.yaml and clears the config cache."}
          </p>
          <Button type="submit" size="lg" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving…" : "Save & continue"} <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <Label className="mb-1.5 block">{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground mt-1.5">{hint}</p>}
    </div>
  );
}

function FieldError({ name, errors }: { name: string; errors: Record<string, { message?: string } | undefined> }) {
  const err = errors[name];
  if (!err?.message) return null;
  return <p className="text-xs text-destructive mt-1">{err.message}</p>;
}

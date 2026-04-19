"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { KbDropzone, type KbFilePreview } from "@/components/upload/kb-dropzone";
import { Stepper } from "@/components/wizard/stepper";
import { api } from "@/lib/api";
import { useWizardStore } from "@/store/wizard-store";

export default function UploadPage() {
  const router = useRouter();
  const markCompleted = useWizardStore((s) => s.markCompleted);

  const [knowledge, setKnowledge] = React.useState<KbFilePreview | null>(null);
  const [tool, setTool] = React.useState<KbFilePreview | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      if (knowledge?.ok) form.append("knowledge_file", knowledge.file);
      if (tool?.ok) form.append("tool_file", tool.file);
      return api.uploadKB(form);
    },
    onSuccess: (data) => {
      const warnings = data.warnings.length > 0 ? ` (${data.warnings.join("; ")})` : "";
      toast.success(`Uploaded successfully${warnings}`);
      markCompleted("upload", true);
      setTimeout(() => router.push("/domain"), 400);
    },
    onError: (err: Error) => {
      toast.error(`Upload failed: ${err.message}`);
    },
  });

  const canSubmit = (knowledge?.ok || tool?.ok) && !mutation.isPending;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Upload knowledge bases</h1>
          <p className="text-muted-foreground text-sm">
            Drop in the two source JSON files. We'll persist them locally and mirror to Vercel Blob for durability.
          </p>
        </div>
        <Stepper current="upload" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UploadCloud className="h-5 w-5" /> Source files
          </CardTitle>
          <CardDescription>
            Both KBs must share the same JSON shape: an object with a top-level <code className="font-mono text-xs">chapters: [...]</code> array.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <KbDropzone
            label="Knowledge KB"
            helpText="Domain policy / reference documentation"
            value={knowledge}
            onChange={setKnowledge}
            accentColor="bg-gradient-to-br from-sky-500/10 to-indigo-500/10"
          />
          <KbDropzone
            label="Tool KB"
            helpText="Systems, tools, integrations catalog"
            value={tool}
            onChange={setTool}
            accentColor="bg-gradient-to-br from-emerald-500/10 to-teal-500/10"
          />
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {knowledge?.ok && tool?.ok
            ? "Both KBs valid. Ready to continue."
            : knowledge?.ok || tool?.ok
              ? "One KB valid — you can upload one at a time or both together."
              : "Waiting for JSON files."}
        </p>
        <Button size="lg" onClick={() => mutation.mutate()} disabled={!canSubmit}>
          {mutation.isPending ? "Uploading…" : "Upload & continue"} <ArrowRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

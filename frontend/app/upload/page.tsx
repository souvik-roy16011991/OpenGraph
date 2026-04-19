"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, UploadCloud, Trash2, FileJson, Briefcase, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { KbDropzone, type KbFilePreview } from "@/components/upload/kb-dropzone";
import { Stepper } from "@/components/wizard/stepper";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import { useWizardStore } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

export default function UploadPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const markCompleted = useWizardStore((s) => s.markCompleted);
  const activeWs = useWorkspaceStore((s) => s.activeId);

  React.useEffect(() => {
    if (!activeWs) router.replace("/workspaces");
  }, [activeWs, router]);

  const [knowledge, setKnowledge] = React.useState<KbFilePreview[]>([]);
  const [tool, setTool] = React.useState<KbFilePreview[]>([]);

  const filesQuery = useQuery({
    queryKey: ["workspace-files", activeWs],
    queryFn: () => api.listWorkspaceFiles(activeWs!),
    enabled: Boolean(activeWs),
    refetchInterval: 5_000,
  });

  const deleteMut = useMutation({
    mutationFn: ({ ws, id }: { ws: string; id: number }) => api.deleteWorkspaceFile(ws, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workspace-files", activeWs] }),
    onError: (err: Error) => toast.error(err.message),
  });

  const uploadMut = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      for (const k of knowledge) if (k.ok) form.append("knowledge_files", k.file);
      for (const t of tool) if (t.ok) form.append("tool_files", t.file);
      return api.uploadKB(form);
    },
    onSuccess: (data) => {
      const newCount = (data.knowledge?.length ?? 0) + (data.tool?.length ?? 0);
      const dupCount = [...(data.knowledge ?? []), ...(data.tool ?? [])].filter((f) => f.duplicate).length;
      toast.success(
        `Uploaded ${newCount} file${newCount === 1 ? "" : "s"}${dupCount ? ` (${dupCount} already existed)` : ""}`
      );
      markCompleted("upload", true);
      setKnowledge([]);
      setTool([]);
      qc.invalidateQueries({ queryKey: ["workspace-files", activeWs] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: (err: Error) => toast.error(`Upload failed: ${err.message}`),
  });

  if (!activeWs) return null;

  const pendingValid = knowledge.filter((k) => k.ok).length + tool.filter((t) => t.ok).length;
  const existingFiles = filesQuery.data?.files ?? [];
  const existingK = existingFiles.filter((f) => f.kb_source === "knowledge");
  const existingT = existingFiles.filter((f) => f.kb_source === "tool");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Upload knowledge bases</h1>
          <p className="text-muted-foreground text-sm">
            Drop one or more JSONs per category. All files for this workspace are merged into a single
            graph at build time.
          </p>
        </div>
        <Stepper current="upload" />
      </div>

      {/* Already uploaded */}
      {(existingK.length > 0 || existingT.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Briefcase className="h-4 w-4" /> Files already in this workspace
            </CardTitle>
            <CardDescription>
              These participate in the next build. Delete any you don&apos;t want to include.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <FileList title="Knowledge" files={existingK} onDelete={(id) => deleteMut.mutate({ ws: activeWs, id })} />
            <FileList title="Tool" files={existingT} onDelete={(id) => deleteMut.mutate({ ws: activeWs, id })} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UploadCloud className="h-5 w-5" /> Add new files
          </CardTitle>
          <CardDescription>
            Each file must be an object with a top-level <code className="font-mono text-xs">chapters: [...]</code> array.
            Duplicates (same SHA-256 within this workspace) are silently skipped.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <KbDropzone
            label="Knowledge KB files"
            helpText="Domain policy / reference documentation"
            value={knowledge}
            onChange={setKnowledge}
            accentColor="bg-gradient-to-br from-sky-500/10 to-indigo-500/10"
          />
          <KbDropzone
            label="Tool KB files"
            helpText="Systems, tools, integrations catalog"
            value={tool}
            onChange={setTool}
            accentColor="bg-gradient-to-br from-emerald-500/10 to-teal-500/10"
          />
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground flex items-center gap-1">
          {pendingValid === 0
            ? "Drop at least one file to enable upload."
            : <>
                <AlertTriangle className="h-3 w-3 text-amber-500" />
                {pendingValid} file{pendingValid === 1 ? "" : "s"} staged to upload into this workspace.
              </>}
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="lg"
            disabled={existingFiles.length === 0}
            onClick={() => router.push("/domain")}
          >
            Skip to domain <ArrowRight className="h-4 w-4" />
          </Button>
          <Button
            size="lg"
            onClick={() => uploadMut.mutate()}
            disabled={pendingValid === 0 || uploadMut.isPending}
          >
            {uploadMut.isPending ? "Uploading…" : `Upload ${pendingValid} file${pendingValid === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function FileList({
  title,
  files,
  onDelete,
}: {
  title: string;
  files: Array<{ id: number; filename: string; size_bytes: number; chapters: number; title?: string | null; blob_url?: string | null }>;
  onDelete: (id: number) => void;
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground uppercase tracking-wider font-medium mb-2">
        {title} · {files.length}
      </p>
      {files.length === 0 ? (
        <p className="text-xs text-muted-foreground italic">None uploaded yet</p>
      ) : (
        <ul className="space-y-1.5">
          {files.map((f) => (
            <li
              key={f.id}
              className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm"
            >
              <FileJson className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="truncate font-medium">{f.filename}</p>
                <p className="text-[10px] text-muted-foreground">
                  {formatBytes(f.size_bytes)} · {f.chapters} ch
                  {f.blob_url ? " · mirrored" : ""}
                </p>
              </div>
              {f.blob_url && <Badge variant="outline" className="text-[9px]">blob</Badge>}
              <button
                onClick={() => onDelete(f.id)}
                className="text-muted-foreground hover:text-destructive"
                title="Remove from workspace"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

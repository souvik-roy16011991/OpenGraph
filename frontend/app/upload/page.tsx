"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, UploadCloud, Trash2, FileText, Briefcase, AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { KbDropzone, type KbFilePreview } from "@/components/upload/kb-dropzone";
import { ParseProgressCard } from "@/components/upload/parse-progress-card";
import { ParseBatchProgressCard } from "@/components/upload/parse-batch-progress-card";
import { WizardPage } from "@/components/wizard/wizard-page";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import { useWizardStore } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

export default function UploadPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const markCompleted = useWizardStore((s) => s.markCompleted);
  // AppShell overlays the workspace-picker modal when this is null, so we
  // don't redirect away from /upload any more — the user stays here and
  // picks a workspace in place.
  const activeWs = useWorkspaceStore((s) => s.activeId);

  const [knowledge, setKnowledge] = React.useState<KbFilePreview[]>([]);
  const [tool, setTool] = React.useState<KbFilePreview[]>([]);

  // Raw-doc parse jobs we've kicked off but haven't yet seen land as
  // WorkspaceFiles. Each upload mutation may add to this list; the
  // ParseProgressCards self-dismiss on terminal status.
  const [parseCards, setParseCards] = React.useState<
    Array<{ jobId: string; filename: string; sizeBytes: number; kbSource: "knowledge" | "tool" }>
  >([]);

  const filesQuery = useQuery({
    queryKey: ["workspace-files", activeWs],
    queryFn: () => api.listWorkspaceFiles(activeWs!),
    enabled: Boolean(activeWs),
    refetchInterval: 5_000,
  });

  const deleteMut = useMutation({
    mutationFn: ({ ws, id }: { ws: string; id: number }) => api.deleteWorkspaceFile(ws, id),
    onMutate: async ({ id }) => {
      const key = ["workspace-files", activeWs] as const;
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<{ files: Array<{ id: number }> }>(key);
      if (prev) {
        qc.setQueryData(key, { ...prev, files: prev.files.filter((f) => f.id !== id) });
      }
      return { prev };
    },
    onError: (err: Error, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["workspace-files", activeWs], ctx.prev);
      toast.error(err.message);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["workspace-files", activeWs] }),
  });

  // Batch-size heuristic. The server processes 8 files concurrently per
  // request. 20 per batch keeps each HTTP round-trip under ~10 s while
  // still giving the user visible progress when they drop 200 files —
  // 10 batches of 20, shown as "Uploading 60/200...".
  const UPLOAD_BATCH_SIZE = 20;
  const [uploadBatch, setUploadBatch] = React.useState<{ current: number; total: number; sent: number; of: number } | null>(null);

  const uploadMut = useMutation({
    mutationFn: async () => {
      // Flatten once so we control interleaving. kb_source sticks with
      // each file so the chunked requests preserve category.
      const items: Array<{ file: File; kb: "knowledge" | "tool" }> = [
        ...knowledge.filter((k) => k.ok).map((k) => ({ file: k.file, kb: "knowledge" as const })),
        ...tool.filter((t) => t.ok).map((t) => ({ file: t.file, kb: "tool" as const })),
      ];

      const totalFiles = items.length;
      if (totalFiles === 0) {
        throw new Error("No valid files to upload.");
      }

      const aggregated = {
        workspace_id: "",
        knowledge: [] as NonNullable<Awaited<ReturnType<typeof api.uploadKB>>["knowledge"]>,
        tool: [] as NonNullable<Awaited<ReturnType<typeof api.uploadKB>>["tool"]>,
        warnings: [] as string[],
      };

      const totalBatches = Math.ceil(totalFiles / UPLOAD_BATCH_SIZE);
      setUploadBatch({ current: 0, total: totalBatches, sent: 0, of: totalFiles });

      for (let b = 0; b < totalBatches; b++) {
        const slice = items.slice(b * UPLOAD_BATCH_SIZE, (b + 1) * UPLOAD_BATCH_SIZE);
        const form = new FormData();
        for (const it of slice) {
          form.append(it.kb === "knowledge" ? "knowledge_files" : "tool_files", it.file);
        }
        const resp = await api.uploadKB(form);
        aggregated.workspace_id = resp.workspace_id;
        aggregated.knowledge.push(...(resp.knowledge ?? []));
        aggregated.tool.push(...(resp.tool ?? []));
        aggregated.warnings.push(...(resp.warnings ?? []));
        setUploadBatch({
          current: b + 1,
          total: totalBatches,
          sent: Math.min(totalFiles, (b + 1) * UPLOAD_BATCH_SIZE),
          of: totalFiles,
        });
      }

      return aggregated;
    },
    onSuccess: (data) => {
      const all = [...(data.knowledge ?? []), ...(data.tool ?? [])];
      const dupCount = all.filter((f) => f.duplicate).length;
      const queued = all.filter((f) => f.parse_job_id && f.status === "queued");
      const done = all.filter((f) => !f.parse_job_id);
      const warnCount = data.warnings?.length ?? 0;

      const msgParts: string[] = [];
      if (done.length) msgParts.push(`Uploaded ${done.length} file${done.length === 1 ? "" : "s"}`);
      if (queued.length) msgParts.push(`queued ${queued.length} for parsing`);
      if (dupCount) msgParts.push(`${dupCount} already existed`);
      if (warnCount) msgParts.push(`${warnCount} failed`);
      toast.success(msgParts.length ? msgParts.join(" · ") : "Upload complete");

      // Surface failures individually so the user knows which files were skipped.
      if (warnCount) {
        for (const w of (data.warnings ?? []).slice(0, 5)) toast.error(w);
        if (warnCount > 5) toast.error(`…and ${warnCount - 5} more failure${warnCount - 5 === 1 ? "" : "s"}`);
      }

      if (queued.length) {
        setParseCards((prev) => [
          ...prev,
          ...queued.map((f) => ({
            jobId: f.parse_job_id!,
            filename: f.filename,
            sizeBytes: f.size_bytes,
            kbSource: f.kb_source,
          })),
        ]);
      }

      markCompleted("upload", true);
      setKnowledge([]);
      setTool([]);
      setUploadBatch(null);
      qc.invalidateQueries({ queryKey: ["workspace-files", activeWs] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: (err: Error) => {
      setUploadBatch(null);
      toast.error(`Upload failed: ${err.message}`);
    },
  });

  if (!activeWs) return null;

  const pendingValid = knowledge.filter((k) => k.ok).length + tool.filter((t) => t.ok).length;
  const existingFiles = filesQuery.data?.files ?? [];
  const existingK = existingFiles.filter((f) => f.kb_source === "knowledge");
  const existingT = existingFiles.filter((f) => f.kb_source === "tool");

  return (
    <WizardPage
      icon={UploadCloud}
      title="Upload knowledge bases"
      description={
        <>
          Drop one or more files per category. All files for this workspace
          are merged into a single graph at build time.
        </>
      }
    >
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

      {/* In-flight vision-OCR parse jobs.
          ≥ 10 jobs → single aggregate card with one batched poll.
          < 10 jobs → one detailed card per job (the existing UX). */}
      {parseCards.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <UploadCloud className="h-4 w-4" /> Parsing uploaded documents
            </CardTitle>
            <CardDescription>
              Vision OCR turns each page into structured JSON. Page count drives
              the time — typically under a minute per 20 pages.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {parseCards.length >= 10 ? (
              <ParseBatchProgressCard
                jobIds={parseCards.map((c) => c.jobId)}
                workspaceId={activeWs}
                onDismissAll={() => setParseCards([])}
              />
            ) : (
              parseCards.map((c) => (
                <ParseProgressCard
                  key={c.jobId}
                  jobId={c.jobId}
                  filename={c.filename}
                  sizeBytes={c.sizeBytes}
                  workspaceId={activeWs}
                  onDismiss={() =>
                    setParseCards((prev) => prev.filter((x) => x.jobId !== c.jobId))
                  }
                />
              ))
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UploadCloud className="h-5 w-5" /> Add new files
          </CardTitle>
          <CardDescription>
            Duplicates (same SHA-256 within this workspace) are silently skipped.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <KbDropzone
            label="Knowledge base files"
            helpText="Domain policy / reference documentation"
            value={knowledge}
            onChange={setKnowledge}
            accentColor="bg-gradient-to-br from-sky-500/10 to-indigo-500/10"
          />
          <KbDropzone
            label="Tool knowledge base files"
            helpText="Systems, tools, integrations catalog"
            value={tool}
            onChange={setTool}
            accentColor="bg-gradient-to-br from-emerald-500/10 to-teal-500/10"
          />
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground flex items-center gap-1">
          {uploadBatch
            ? <>
              <AlertTriangle className="h-3 w-3 text-sky-500" />
              Uploading {uploadBatch.sent}/{uploadBatch.of}
              {uploadBatch.total > 1 ? ` (batch ${uploadBatch.current}/${uploadBatch.total})` : ""}…
            </>
            : pendingValid === 0
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
            {uploadMut.isPending
              ? uploadBatch
                ? `Uploading ${uploadBatch.sent}/${uploadBatch.of}…`
                : "Uploading…"
              : `Upload ${pendingValid} file${pendingValid === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>
    </WizardPage>
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
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />

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

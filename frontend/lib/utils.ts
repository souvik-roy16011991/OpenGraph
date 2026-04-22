import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatDuration(sec: number) {
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

/** Human-friendly number formatting: 980 -> "980", 12340 -> "12.3K",
 *  1_234_567 -> "1.23M". Used for token counts and vector counts where
 *  raw numbers eat horizontal space. */
export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  return `${(n / 1_000_000_000).toFixed(2)}B`;
}

/** Like formatDuration but takes milliseconds and returns sub-second precision.
 *  45000 -> "45.0s", 1_200 -> "1.20s", 120 -> "120ms". */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return formatDuration(ms / 1000);
}

/** Map internal backend identifiers (as written into ``BuildJobRow.backends``
 *  and ``stats.backends``) to user-facing labels. Keeps the data layer stable
 *  — internal names like "memgraph" / "qdrant" / "neo4j" stay in the JSONB
 *  while the UI always reads "graph store" / "vector DB".
 */
export function backendLabel(raw: string | null | undefined): string {
  if (!raw) return "—";
  const key = String(raw).toLowerCase();
  if (["memgraph", "neo4j"].includes(key)) return "graph store";
  if (["qdrant", "pinecone"].includes(key)) return "vector DB";
  if (["networkx", "faiss"].includes(key)) return "in-memory";
  return raw;
}

/** Extract a clean user-facing message from an unknown thrown value.
 *  `String(new Error("x"))` returns "Error: x" — we want just "x". */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return "Something went wrong. Please refresh the page and try again.";
}

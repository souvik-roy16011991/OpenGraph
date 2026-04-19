"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useBuildJob(jobId: string | null) {
  return useQuery({
    queryKey: ["build", jobId],
    queryFn: () => api.buildStatus(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return 1500;
      if (data.status === "running" || data.status === "queued") return 1500;
      return false;
    },
  });
}

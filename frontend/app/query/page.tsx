"use client";

import { MessageSquareText } from "lucide-react";
import { Stepper } from "@/components/wizard/stepper";
import { Chat } from "@/components/query/chat";
import { useRequireWorkspace } from "@/hooks/use-require-workspace";

export default function QueryPage() {
  const activeWs = useRequireWorkspace();
  if (!activeWs) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <MessageSquareText className="h-5 w-5" /> Ask the agent
          </h1>
          <p className="text-muted-foreground text-sm">
            Natural-language queries over the graph — the agent classifies intent, picks entry nodes, traverses, and synthesizes.
          </p>
        </div>
        <Stepper current="query" />
      </div>
      <Chat />
    </div>
  );
}

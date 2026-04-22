"use client";

import { History } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BuildsTable } from "@/components/history/builds-table";
import { ChatsTable } from "@/components/history/chats-table";
import { ConfigsTable } from "@/components/history/configs-table";
import { UploadsTable } from "@/components/history/uploads-table";
import { HistoryDashboard } from "@/components/history/history-dashboard";
import { useRequireWorkspace } from "@/hooks/use-require-workspace";

export default function HistoryPage() {
  // History data is workspace-scoped — every child query sends X-Workspace-Id.
  // When no workspace is active (fresh sign-in, or after clearSession wiped
  // the persisted active id), bounce to /workspaces instead of letting the
  // table queries fire without the header and 400 on "Missing X-Workspace-Id".
  const activeId = useRequireWorkspace();
  if (!activeId) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <History className="h-5 w-5" /> History
        </h1>
      </div>

      <HistoryDashboard />

      <Tabs defaultValue="builds">
        <TabsList>
          <TabsTrigger value="builds">Builds</TabsTrigger>
          <TabsTrigger value="chats">Chats</TabsTrigger>
          <TabsTrigger value="configs">Configs</TabsTrigger>
          <TabsTrigger value="uploads">Uploads</TabsTrigger>
        </TabsList>
        <TabsContent value="builds"><BuildsTable /></TabsContent>
        <TabsContent value="chats"><ChatsTable /></TabsContent>
        <TabsContent value="configs"><ConfigsTable /></TabsContent>
        <TabsContent value="uploads"><UploadsTable /></TabsContent>
      </Tabs>
    </div>
  );
}

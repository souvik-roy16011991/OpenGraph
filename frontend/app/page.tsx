"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Upload, Tag, Sliders, Hammer, Network, MessageSquareText, ArrowRight, Briefcase } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace-store";

const STEPS = [
  { n: 1, href: "/upload",       icon: Upload,            title: "Upload KB files",  desc: "Drop one or more JSONs — knowledge and/or tool." },
  { n: 2, href: "/domain",       icon: Tag,               title: "Configure domain", desc: "Name the domain, organization, and focus areas." },
  { n: 3, href: "/graph-config", icon: Sliders,           title: "Tune graph knobs", desc: "Adjust how dense, how deep, how precise the graph should be." },
  { n: 4, href: "/build",        icon: Hammer,            title: "Run the build",    desc: "Embeddings, edges, cross-KB links. Watch it happen." },
  { n: 5, href: "/explore",      icon: Network,           title: "Explore visually", desc: "Zoom, filter, and inspect every node of the finished graph." },
  { n: 6, href: "/query",        icon: MessageSquareText, title: "Ask the agent",    desc: "Natural-language queries over the graph with traced reasoning." },
];

export default function Home() {
  const router = useRouter();
  const activeId = useWorkspaceStore((s) => s.activeId);

  // If no workspace is active, send the user to the workspace picker first.
  React.useEffect(() => {
    if (!activeId) router.replace("/workspaces");
  }, [activeId, router]);

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div className="inline-flex items-center gap-2 text-xs font-mono uppercase tracking-wider text-muted-foreground">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
          Knowledge Graph Engine
        </div>
        <h1 className="text-3xl md:text-4xl font-semibold tracking-tight max-w-3xl">
          One workspace per domain. Many files per workspace. Isolated graph &amp; chat per workspace.
        </h1>
        <p className="text-muted-foreground max-w-2xl">
          Create a workspace, drop in any number of knowledge + tool JSONs, tune the graph parameters,
          build, and query. Every workspace has its own Memgraph partition, its own Qdrant collection,
          and its own chat history.
        </p>
        <div className="flex gap-3 pt-2">
          <Button asChild size="lg">
            <Link href="/workspaces"><Briefcase className="h-4 w-4" /> Manage workspaces</Link>
          </Button>
          {activeId && (
            <Button asChild variant="outline" size="lg">
              <Link href="/upload">Continue current workspace <ArrowRight className="h-4 w-4" /></Link>
            </Button>
          )}
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {STEPS.map((s) => (
          <Link key={s.n} href={activeId ? s.href : "/workspaces"}>
            <Card className="group hover:border-primary/50 hover:shadow-md transition cursor-pointer h-full">
              <CardHeader className="space-y-2">
                <div className="flex items-center gap-3">
                  <div className="h-9 w-9 rounded-md bg-secondary flex items-center justify-center group-hover:bg-primary/10">
                    <s.icon className="h-4 w-4" />
                  </div>
                  <span className="font-mono text-xs text-muted-foreground">Step 0{s.n}</span>
                </div>
                <CardTitle className="text-lg">{s.title}</CardTitle>
                <CardDescription>{s.desc}</CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="inline-flex items-center gap-1 text-xs text-primary/70 group-hover:text-primary">
                  Open <ArrowRight className="h-3 w-3" />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}

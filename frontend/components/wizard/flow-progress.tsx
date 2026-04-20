"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Check, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWizardStore, type WizardStep } from "@/store/wizard-store";

interface FlowStep {
  step: WizardStep;
  href: string;
  label: string;
  requires: WizardStep[];
}

const STEPS: FlowStep[] = [
  { step: "upload",       href: "/upload",       label: "Upload KB",    requires: [] },
  { step: "domain",       href: "/domain",       label: "Domain",       requires: ["upload"] },
  { step: "graph-config", href: "/graph-config", label: "Graph Config", requires: ["upload", "domain"] },
  { step: "build",        href: "/build",        label: "Build",        requires: ["upload", "domain"] },
  { step: "explore",      href: "/explore",      label: "Explore",      requires: ["build"] },
];

const WIZARD_ROUTES = STEPS.map((s) => s.href);

export function isWizardRoute(pathname: string | null): boolean {
  if (!pathname) return false;
  return WIZARD_ROUTES.some((r) => pathname.startsWith(r));
}

export function FlowProgress() {
  const pathname = usePathname();
  const completed = useWizardStore((s) => s.completed);

  if (!isWizardRoute(pathname)) return null;

  const currentIdx = STEPS.findIndex((s) => pathname?.startsWith(s.href));
  const current = STEPS[Math.max(0, currentIdx)];

  return (
    <div className="border-b bg-card/30 px-6 md:px-8 py-3">
      <div className="mx-auto max-w-7xl flex items-center gap-3">
        <ol className="flex items-center flex-1 min-w-0">
          {STEPS.map((s, i) => {
            const isCurrent = s.step === current.step;
            const isDone = completed[s.step];
            const locked = s.requires.some((r) => !completed[r]);
            const clickable = !locked;

            const dot = (
              <span
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-mono border transition-colors shrink-0",
                  isDone && "bg-emerald-500 border-emerald-500 text-white",
                  !isDone && isCurrent && "bg-primary border-primary text-primary-foreground",
                  !isDone && !isCurrent && !locked && "bg-background border-border text-muted-foreground",
                  !isDone && !isCurrent && locked && "bg-background border-dashed border-border text-muted-foreground/60",
                )}
              >
                {isDone ? <Check className="h-3 w-3" /> : locked ? <Lock className="h-3 w-3" /> : i + 1}
              </span>
            );

            const label = (
              <span
                className={cn(
                  "text-[12px] font-medium whitespace-nowrap transition-colors",
                  isCurrent && "text-foreground",
                  !isCurrent && isDone && "text-foreground/80",
                  !isCurrent && !isDone && "text-muted-foreground",
                  locked && "text-muted-foreground/60",
                )}
              >
                {s.label}
              </span>
            );

            const content = (
              <span className="flex items-center gap-2">
                {dot}
                {label}
              </span>
            );

            return (
              <React.Fragment key={s.step}>
                <li className="shrink-0">
                  {clickable ? (
                    <Link
                      href={s.href}
                      className="flex items-center rounded-md px-1 py-1 hover:bg-accent/50 transition-colors"
                    >
                      {content}
                    </Link>
                  ) : (
                    <span
                      aria-disabled="true"
                      className="flex items-center rounded-md px-1 py-1 cursor-not-allowed"
                      title="Complete previous steps first"
                    >
                      {content}
                    </span>
                  )}
                </li>
                {i < STEPS.length - 1 && (
                  <li
                    aria-hidden
                    className={cn(
                      "h-px flex-1 min-w-[16px] mx-1 transition-colors",
                      completed[s.step] && completed[STEPS[i + 1].step] && "bg-emerald-500/60",
                      completed[s.step] && !completed[STEPS[i + 1].step] && "bg-primary/40",
                      !completed[s.step] && "bg-border",
                    )}
                  />
                )}
              </React.Fragment>
            );
          })}
        </ol>

        <div className="hidden md:flex items-center gap-1 text-[11px] text-muted-foreground font-mono shrink-0 pl-2 border-l ml-2">
          <span>Step {currentIdx + 1} of {STEPS.length}</span>
          <span className="text-foreground/70">·</span>
          <span className="text-foreground/80">{current.label}</span>
        </div>
      </div>
    </div>
  );
}

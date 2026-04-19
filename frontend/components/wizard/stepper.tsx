"use client";

import * as React from "react";
import Link from "next/link";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWizardStore, type WizardStep } from "@/store/wizard-store";

const STEPS: { step: WizardStep; href: string; label: string }[] = [
  { step: "upload", href: "/upload", label: "Upload" },
  { step: "domain", href: "/domain", label: "Domain" },
  { step: "graph-config", href: "/graph-config", label: "Graph config" },
  { step: "build", href: "/build", label: "Build" },
  { step: "explore", href: "/explore", label: "Explore" },
];

export function Stepper({ current }: { current: WizardStep }) {
  const completed = useWizardStore((s) => s.completed);
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((s, i) => {
        const isCurrent = s.step === current;
        const isDone = completed[s.step];
        return (
          <React.Fragment key={s.step}>
            <li>
              <Link
                href={s.href}
                className={cn(
                  "flex items-center gap-2 rounded-full border px-3 py-1 transition-colors",
                  isCurrent && "bg-primary text-primary-foreground border-primary",
                  !isCurrent && isDone && "border-emerald-500/50 text-emerald-700 dark:text-emerald-400",
                  !isCurrent && !isDone && "text-muted-foreground hover:border-foreground/40"
                )}
              >
                <span
                  className={cn(
                    "inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-mono",
                    isDone ? "bg-emerald-500 text-white" : isCurrent ? "bg-background/20" : "bg-muted"
                  )}
                >
                  {isDone ? <Check className="h-2.5 w-2.5" /> : i + 1}
                </span>
                {s.label}
              </Link>
            </li>
            {i < STEPS.length - 1 && <li className="h-[1px] w-4 bg-border" />}
          </React.Fragment>
        );
      })}
    </ol>
  );
}

"use client";

import * as React from "react";

/**
 * Shared wizard page chrome.
 *
 * Keeps the four wizard steps (/upload, /domain, /graph-config, /build)
 * visually identical at the top: same heading size, same description
 * spacing, same max width. Drop-in header so no page owns a bespoke
 * copy of this markup.
 *
 * Usage:
 *     <WizardPage
 *       icon={Hammer}
 *       title="Build your graph"
 *       description="Parse, extract, embed, persist."
 *       right={<Button>Action</Button>}  // optional trailing chip
 *     >
 *       …body content…
 *     </WizardPage>
 */
export function WizardPage({
  icon: Icon,
  title,
  description,
  right,
  children,
  /** Override the default body max-width. Most wizard pages are form-heavy
   *  so 3xl (48rem) reads well; graph-config needs more room for the
   *  tabbed knobs. */
  maxWidthClass = "max-w-3xl",
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  maxWidthClass?: string;
}) {
  return (
    <div className={`space-y-6 ${maxWidthClass}`}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            {Icon && <Icon className="h-5 w-5" />}
            {title}
          </h1>
          {description && (
            <p className="text-sm text-muted-foreground mt-1">{description}</p>
          )}
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
      {children}
    </div>
  );
}

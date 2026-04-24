"use client";

import * as React from "react";
import { CalendarCheck2, ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { CAL_BOOKING_URL } from "@/lib/plans";
import { cn } from "@/lib/utils";

/**
 * One button every Enterprise CTA on the site points at.
 *
 * Opens the co-founder's Cal.com booking link in a new tab — no
 * third-party script embed, no popup permission dance, no CSP
 * surprise. The consistent affordance is why every callout reads the
 * same way: "Book a call".
 */
export function BookCallButton({
  size = "default",
  variant = "default",
  label = "Book a call",
  className,
}: {
  size?: "sm" | "default" | "lg";
  variant?: "default" | "outline" | "ghost";
  label?: string;
  className?: string;
}) {
  return (
    <Button
      asChild
      size={size}
      variant={variant}
      className={cn("gap-2", className)}
    >
      <a
        href={CAL_BOOKING_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        <CalendarCheck2 className="h-4 w-4" />
        {label}
        <ExternalLink className="h-3 w-3 opacity-70" />
      </a>
    </Button>
  );
}

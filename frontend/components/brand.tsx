import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * OpenGraph brand primitives.
 *
 * - <BrandMark /> — the inverted-triangle glyph. Scales from 16px to 64px
 *   without rasterising (SVG). Honors `currentColor` via the optional
 *   ``tinted`` prop so it can sit on dark sidebars without a white halo.
 * - <BrandWordmark /> — glyph + "OpenGraph" text. Used wherever the brand
 *   identifies itself (sidebar header, landing hero, auth pages).
 */

export const BRAND_COLOR = "#2F4B1A"; // deep olive-green from the attached brand image

export function BrandMark({
  className,
  size = 20,
  tinted = false,
}: {
  className?: string;
  size?: number;
  tinted?: boolean;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-label="OpenGraph"
      role="img"
      className={cn("shrink-0", className)}
    >
      <path d="M12 20L4 6h16z" fill={tinted ? "currentColor" : BRAND_COLOR} />
    </svg>
  );
}

export function BrandWordmark({
  className,
  size = "md",
  tinted = false,
}: {
  className?: string;
  size?: "sm" | "md" | "lg";
  tinted?: boolean;
}) {
  const mark = size === "lg" ? 28 : size === "sm" ? 16 : 22;
  const text =
    size === "lg"
      ? "text-xl font-semibold"
      : size === "sm"
      ? "text-sm font-semibold"
      : "text-base font-semibold";
  return (
    <div className={cn("inline-flex items-center gap-2", className)}>
      <BrandMark size={mark} tinted={tinted} />
      <span className={cn("tracking-tight leading-none", text)}>OpenGraph</span>
    </div>
  );
}

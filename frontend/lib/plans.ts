/**
 * Plan constants — single source of truth for the two user-facing plans.
 *
 * The backend's ``BillingAccount.plan_tier`` enum still carries three
 * values (``trial | payg | team``) for internal accounting, but users
 * see exactly two tiers:
 *
 *   - **Free trial** — the default; what ``plan_tier=trial`` maps to.
 *   - **Enterprise** — everything else (``payg``, ``team``). Upgrade is
 *     a contact-sales flow: the user books time with the co-founder
 *     via Cal.com and we handle the rest off-platform.
 *
 * Keeping the Cal.com URL + copy in one place means changing the
 * booking link or the trial entitlements never requires a hunt across
 * the UI — touch this file and the billing page + sign-up + 402 toast
 * all stay in sync.
 */

import type { BillingSummary } from "@/lib/schema";

/** Co-founder booking link. Opened in a new tab with rel=noopener. */
export const CAL_BOOKING_URL =
  "https://cal.com/souvik-roy-t4tzgc/let-s-connect-to-understand-your-ai-hussle?overlayCalendar=true";

/** Keep these bullets tight — they show up in billing tiles + marketing. */
export const FREE_TRIAL_INCLUDES: readonly string[] = [
  "1 workspace",
  "1 graph build",
  "5 chat queries",
  "Vision OCR ingestion (PDF, PPTX, DOCX…)",
  "All graph, domain, and chat features",
];

export const ENTERPRISE_INCLUDES: readonly string[] = [
  "Unlimited workspaces, builds, and chats",
  "Higher upload caps + parse concurrency",
  "Priority OpenRouter routing",
  "Dedicated co-founder support channel",
  "Custom SSO / on-prem deploy on request",
];

/** Normalise the backend's three tiers to the two visible ones. */
export function planToDisplay(
  tier: BillingSummary["plan_tier"] | undefined,
): "free_trial" | "enterprise" {
  return tier === "trial" || tier === undefined ? "free_trial" : "enterprise";
}

/** Human-readable label for the active plan. */
export function planLabel(tier: BillingSummary["plan_tier"] | undefined): string {
  return planToDisplay(tier) === "free_trial" ? "Free trial" : "Enterprise";
}

/**
 * Open the booking link. Centralised so we can swap in an embedded
 * Cal.com popover later without touching call sites.
 */
export function openBooking(): void {
  if (typeof window === "undefined") return;
  window.open(CAL_BOOKING_URL, "_blank", "noopener,noreferrer");
}

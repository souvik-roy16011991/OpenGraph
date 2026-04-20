import { redirect } from "next/navigation";

/**
 * Legacy /query route — the chat UX moved to /chat as a standalone
 * Playground (separate from the build wizard). We keep this entry so
 * existing bookmarks survive.
 */
export default function LegacyQueryRedirect() {
  redirect("/chat");
}

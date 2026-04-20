import { redirect } from "next/navigation";

/** Convenience shortcut. The authoritative sign-in UI is Stack's handler. */
export default function SignInShortcut() {
  redirect("/handler/sign-in");
}

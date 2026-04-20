import { redirect } from "next/navigation";

/** Convenience shortcut. The authoritative sign-up UI is Stack's handler. */
export default function SignUpShortcut() {
  redirect("/handler/sign-up");
}

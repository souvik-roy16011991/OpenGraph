import { redirect } from "next/navigation";

/** Sign-up entry point — forwards to Neon Auth's /handler flow. */
export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const rawReturn = params?.return_to;
  const returnTo = typeof rawReturn === "string" ? rawReturn : undefined;
  const target = returnTo
    ? `/handler/sign-up?after_auth_return_to=${encodeURIComponent(returnTo)}`
    : "/handler/sign-up";
  redirect(target);
}

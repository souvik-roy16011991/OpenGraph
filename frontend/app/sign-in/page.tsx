"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { githubLoginUrl, login } from "@/lib/auth";

function GitHubMark({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="currentColor"
    >
      <path d="M12 .5C5.65.5.5 5.65.5 12a11.5 11.5 0 0 0 7.86 10.94c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.05-.71.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.76 2.7 1.25 3.36.96.1-.74.4-1.25.73-1.54-2.56-.29-5.25-1.28-5.25-5.7 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.47.11-3.07 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.21-1.49 3.18-1.18 3.18-1.18.63 1.6.23 2.78.11 3.07.74.81 1.19 1.84 1.19 3.1 0 4.43-2.69 5.41-5.26 5.69.41.36.78 1.07.78 2.16v3.2c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
    </svg>
  );
}

const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  oauth_incomplete: "We didn't receive a sign-in token. Please try again.",
  oauth_me_failed: "Signed in, but couldn't load your profile. Please try again.",
  oauth_state_invalid: "Sign-in request expired. Please try again.",
  oauth_missing_params: "Something was missing from the GitHub callback. Please try again.",
  oauth_not_configured: "GitHub sign-in isn't configured on this server.",
  oauth_internal_error: "Something went wrong signing you in with GitHub. Please try again.",
  github_denied: "Sign-in was cancelled on GitHub.",
  github_error: "GitHub rejected the sign-in. Please try again.",
  github_exchange_failed: "We couldn't verify your GitHub account. Please try again.",
  github_email_unverified: "Your primary GitHub email isn't verified. Verify it on GitHub and retry.",
};

import { AuthAnimation } from "@/components/auth/auth-animation";
import { BrandMark } from "@/components/brand";

export default function SignInPage() {
  return (
    <React.Suspense fallback={<div className="min-h-screen grid place-items-center bg-background text-muted-foreground text-sm">Loading sign-in...</div>}>
      <SignInPageInner />
    </React.Suspense>
  );
}

function SignInPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const oauthErrorCode = params.get("error");
  const initialError = oauthErrorCode
    ? (OAUTH_ERROR_MESSAGES[oauthErrorCode] ?? "Sign-in with GitHub failed. Please try again.")
    : null;
  const [error, setError] = React.useState<string | null>(initialError);
  const [submitting, setSubmitting] = React.useState(false);

  // Calculate density based on form completion
  const density = (email.length > 3 ? 0.3 : 0) + (password.length > 0 ? 0.3 : 0) + 0.4;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login({ email: email.trim().toLowerCase(), password });
      router.replace(returnTo);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-background">
      {/* Overview Section (Left/Top) */}
      <section className="relative w-full md:w-1/2 lg:w-3/5 bg-muted/30 overflow-hidden border-b md:border-b-0 md:border-r flex flex-col p-8 md:p-12 justify-between">
        <AuthAnimation density={density} />

        <div className="relative z-10">
          <Link href="/" className="inline-flex items-center gap-2 group">
            <div className="h-8 w-8 rounded-md border bg-white flex items-center justify-center shadow-sm group-hover:shadow transition-shadow">
              <BrandMark size={18} />
            </div>
            <span className="font-semibold text-xl tracking-tight">OpenGraph</span>
          </Link>
        </div>

        <div className="relative z-10 max-w-md">
          <h2 className="text-3xl font-semibold tracking-tight mb-4">
            Connect your knowledge.
          </h2>
          <p className="text-muted-foreground leading-relaxed">
            OpenGraph maps your documentation, tools, and processes into a unified
            knowledge graph. Build isolation, multi-model chat, and visual traversal
            shipped in a single workspace.
          </p>
        </div>

      </section>

      {/* Form Section (Right/Bottom) */}
      <section className="flex-1 flex items-center justify-center p-6 md:p-12">
        <Card className="w-full max-w-sm border-none shadow-none bg-transparent">
          <CardHeader className="px-0">
            <CardTitle className="text-2xl">Sign in</CardTitle>
            <CardDescription>Welcome back.</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="w-full mb-4"
              disabled={submitting}
              onClick={() => {
                window.location.href = githubLoginUrl(rawReturn);
              }}
            >
              <GitHubMark className="h-4 w-4 mr-2" />
              Continue with GitHub
            </Button>
            <div className="relative my-4" aria-hidden="true">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-background px-2 text-muted-foreground">Or</span>
              </div>
            </div>
            <form onSubmit={onSubmit} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="name@company.com"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                  className="bg-background"
                />
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label htmlFor="password">Password</Label>
                </div>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                  className="bg-background"
                />
              </div>
              {error ? (
                <p className="text-xs text-destructive" role="alert">{error}</p>
              ) : null}
              <Button type="submit" className="w-full" size="lg" disabled={submitting}>
                {submitting ? "Signing in…" : "Sign in"}
              </Button>
              <p className="text-xs text-muted-foreground text-center">
                Don&apos;t have an account?{" "}
                <Link
                  href={`/sign-up${rawReturn ? `?return_to=${encodeURIComponent(rawReturn)}` : ""}`}
                  className="underline hover:text-foreground font-medium"
                >
                  Sign up
                </Link>
              </p>
            </form>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

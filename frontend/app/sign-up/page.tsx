"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { githubLoginUrl, signup } from "@/lib/auth";

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

import { AuthAnimation } from "@/components/auth/auth-animation";
import { BrandMark } from "@/components/brand";
import { CAL_BOOKING_URL } from "@/lib/plans";

const MIN_PASSWORD = 8;

export default function SignUpPage() {
  return (
    <React.Suspense fallback={<div className="min-h-screen grid place-items-center bg-background text-muted-foreground text-sm">Loading sign-up...</div>}>
      <SignUpPageInner />
    </React.Suspense>
  );
}

function SignUpPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  // Calculate density based on form completion
  const density = (email.length > 3 ? 0.2 : 0) + (displayName.length > 2 ? 0.2 : 0) + (password.length > 0 ? 0.2 : 0) + 0.4;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await signup({
        email: email.trim().toLowerCase(),
        password,
        display_name: displayName.trim() || undefined,
      });
      router.replace(returnTo);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-up failed.");
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
            One workspace per tenant.
          </h2>
          <p className="text-muted-foreground leading-relaxed">
            Create an account to start building your own private knowledge graphs.
            Each email address is isolated into its own secure environment with dedicated
            vector and graph storage.
          </p>
        </div>

      </section>

      {/* Form Section (Right/Bottom) */}
      <section className="flex-1 flex items-center justify-center p-6 md:p-12 overflow-y-auto">
        <Card className="w-full max-w-sm border-none shadow-none bg-transparent my-auto">
          <CardHeader className="px-0 pt-0">
            <CardTitle className="text-2xl">Create an account</CardTitle>
            <CardDescription>Each email is its own private workspace tenant.</CardDescription>
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
                <Label htmlFor="display_name">Display name (optional)</Label>
                <Input
                  id="display_name"
                  type="text"
                  autoComplete="name"
                  placeholder="Jane Doe"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  disabled={submitting}
                  className="bg-background"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                  className="bg-background"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="confirm">Confirm password</Label>
                <Input
                  id="confirm"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  disabled={submitting}
                  className="bg-background"
                />
              </div>
              {error ? (
                <p className="text-xs text-destructive" role="alert">{error}</p>
              ) : null}
              <Button type="submit" className="w-full" size="lg" disabled={submitting}>
                {submitting ? "Creating account…" : "Create account"}
              </Button>
              <p className="text-xs text-muted-foreground text-center pb-2">
                Already have an account?{" "}
                <Link
                  href={`/sign-in${rawReturn ? `?return_to=${encodeURIComponent(rawReturn)}` : ""}`}
                  className="underline hover:text-foreground font-medium"
                >
                  Sign in
                </Link>
              </p>
              {/* Two-plan footer — see frontend/lib/plans.ts. Sign-up lands
                  every user on the free trial; if they already know they
                  need Enterprise (SSO, higher limits, support channel),
                  the Cal.com link goes straight to the co-founder. */}
              <p className="text-[11px] text-muted-foreground text-center pb-4">
                You&apos;ll start on the <span className="font-medium">free trial</span>.
                {" "}Need Enterprise?{" "}
                <a
                  href={CAL_BOOKING_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-foreground font-medium"
                >
                  Book a call
                </a>
                .
              </p>
            </form>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

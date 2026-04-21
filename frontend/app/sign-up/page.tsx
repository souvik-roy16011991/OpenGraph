"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { signup, loginWithOAuth } from "@/lib/auth";
import { AuthAnimation } from "@/components/auth/auth-animation";
import { BrandMark } from "@/components/brand";

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
  const [oauthPending, setOauthPending] = React.useState<"google" | "github" | null>(null);

  const density =
    (email.length > 3 ? 0.2 : 0) +
    (displayName.length > 2 ? 0.2 : 0) +
    (password.length > 0 ? 0.2 : 0) +
    0.4;

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

  async function onOAuth(provider: "google" | "github") {
    setError(null);
    setOauthPending(provider);
    try {
      await loginWithOAuth(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${provider} sign-in failed.`);
      setOauthPending(null);
    }
  }

  const busy = submitting || oauthPending !== null;

  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-background">
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
          <h2 className="text-3xl font-semibold tracking-tight mb-4">One workspace per tenant.</h2>
          <p className="text-muted-foreground leading-relaxed">
            Create an account to start building your own private knowledge graphs.
            Each email address is isolated into its own secure environment with dedicated
            vector and graph storage.
          </p>
        </div>
      </section>

      <section className="flex-1 flex items-center justify-center p-6 md:p-12 overflow-y-auto">
        <Card className="w-full max-w-sm border-none shadow-none bg-transparent my-auto">
          <CardHeader className="px-0 pt-0">
            <CardTitle className="text-2xl">Create an account</CardTitle>
            <CardDescription>Each email is its own private workspace tenant.</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            {/* OAuth buttons */}
            <div className="space-y-2 mb-4">
              <Button
                type="button"
                variant="outline"
                className="w-full gap-2"
                disabled={busy}
                onClick={() => onOAuth("google")}
              >
                <GoogleIcon />
                {oauthPending === "google" ? "Redirecting…" : "Continue with Google"}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="w-full gap-2"
                disabled={busy}
                onClick={() => onOAuth("github")}
              >
                <GitHubIcon />
                {oauthPending === "github" ? "Redirecting…" : "Continue with GitHub"}
              </Button>
            </div>

            <div className="relative my-4">
              <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-background px-2 text-muted-foreground">or</span>
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
                  disabled={busy}
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
                  disabled={busy}
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
                  disabled={busy}
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
                  disabled={busy}
                  className="bg-background"
                />
              </div>
              {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
              <Button type="submit" className="w-full" size="lg" disabled={busy}>
                {submitting ? "Creating account…" : "Create account"}
              </Button>
              <p className="text-xs text-muted-foreground text-center pb-4">
                Already have an account?{" "}
                <Link
                  href={`/sign-in${rawReturn ? `?return_to=${encodeURIComponent(rawReturn)}` : ""}`}
                  className="underline hover:text-foreground font-medium"
                >
                  Sign in
                </Link>
              </p>
            </form>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0 0 22 12.017C22 6.484 17.522 2 12 2z" />
    </svg>
  );
}

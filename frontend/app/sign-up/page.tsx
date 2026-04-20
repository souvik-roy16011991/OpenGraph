"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { signup } from "@/lib/auth";

const MIN_PASSWORD = 8;

export default function SignUpPage() {
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
    <main className="min-h-screen grid place-items-center p-6 bg-background">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Create an account</CardTitle>
          <CardDescription>Each email is its own private workspace tenant.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="display_name">Display name (optional)</Label>
              <Input
                id="display_name"
                type="text"
                autoComplete="name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={submitting}
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
              />
            </div>
            {error ? (
              <p className="text-xs text-destructive" role="alert">{error}</p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Creating account…" : "Create account"}
            </Button>
            <p className="text-xs text-muted-foreground text-center">
              Already have an account?{" "}
              <Link
                href={`/sign-in${rawReturn ? `?return_to=${encodeURIComponent(rawReturn)}` : ""}`}
                className="underline hover:text-foreground"
              >
                Sign in
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

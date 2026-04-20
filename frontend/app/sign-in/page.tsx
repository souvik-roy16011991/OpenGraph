"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login } from "@/lib/auth";

export default function SignInPage() {
  const router = useRouter();
  const params = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

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
    <main className="min-h-screen grid place-items-center p-6 bg-background">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Welcome back.</CardDescription>
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
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />
            </div>
            {error ? (
              <p className="text-xs text-destructive" role="alert">{error}</p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
            <p className="text-xs text-muted-foreground text-center">
              Don&apos;t have an account?{" "}
              <Link
                href={`/sign-up${rawReturn ? `?return_to=${encodeURIComponent(rawReturn)}` : ""}`}
                className="underline hover:text-foreground"
              >
                Sign up
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

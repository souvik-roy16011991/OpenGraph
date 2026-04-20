"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login } from "@/lib/auth";

import { AuthAnimation } from "@/components/auth/auth-animation";
import { BrandMark } from "@/components/brand";

export default function SignInPage() {
  const router = useRouter();
  const params = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = rawReturn && rawReturn.startsWith("/") ? rawReturn : "/";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
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

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function LoginPage() {
  const router = useRouter();
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
        credentials: "include",
      });
      if (res.ok) {
        router.push("/dashboard");
      } else {
        setError("Invalid PIN. Please try again.");
      }
    } catch {
      setError("Connection error. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      className="flex min-h-screen items-center justify-center px-4"
      style={{ background: "radial-gradient(ellipse at 50% 0%, rgba(139,92,246,0.12) 0%, var(--dp-bg) 60%)" }}
    >
      <div className="grid-bg pointer-events-none fixed inset-0 opacity-50" />

      <div className="relative w-full max-w-sm">
        <div className="absolute -inset-1 rounded-2xl bg-orange-500/10 blur-xl" />
        <div className="relative rounded-2xl border border-dp-border bg-dp-s1 p-8 shadow-2xl">
          <div className="mb-8 text-center">
            <Link href="/" className="inline-flex items-center gap-2 mb-6">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-orange-500/20 bg-orange-500/10">
                <svg className="h-5 w-5 text-orange-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z" />
                </svg>
              </div>
              <span className="text-sm font-semibold text-dp-text">DrivePool</span>
            </Link>
            <h1 className="text-2xl font-semibold tracking-tight text-dp-text">Welcome back</h1>
            <p className="mt-1.5 text-sm text-dp-text2">Enter your PIN to access the dashboard</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <input
              type="password"
              placeholder="••••••"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              autoFocus
              className="w-full rounded-xl border border-dp-border bg-dp-bg px-4 py-3 text-center text-xl tracking-[0.5em] text-dp-text placeholder-dp-text3 outline-none transition focus:border-orange-500/50 focus:ring-1 focus:ring-orange-500/30"
            />

            {error && (
              <div className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2.5">
                <svg className="h-4 w-4 flex-shrink-0 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                </svg>
                <p className="text-sm text-red-400">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !pin}
              className="w-full rounded-xl bg-orange-500 py-3 text-sm font-semibold text-white shadow-lg shadow-orange-500/20 transition hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Verifying…
                </span>
              ) : "Unlock Dashboard"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}

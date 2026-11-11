"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Logo } from "@/components/Logo";

export default function LoginPage() {
  const { login, me, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!loading && me) router.replace(me.must_change_password ? "/change-password" : "/"); }, [me, loading, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { const m = await login(email.trim(), password); router.replace(m.must_change_password ? "/change-password" : "/"); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not sign in"); setBusy(false); }
  }

  return (
    <div className="min-h-screen grid md:grid-cols-[1.1fr_1fr]">
      <div className="hidden md:flex flex-col justify-between p-12 text-white" style={{ background: "radial-gradient(900px 600px at 15% 105%, rgb(1 135 122 / 0.28), transparent 60%), radial-gradient(700px 500px at 100% 0%, rgb(255 255 255 / 0.07), transparent 60%), radial-gradient(circle at 1px 1px, rgb(255 255 255 / 0.07) 1px, transparent 1.2px) 0 0 / 24px 24px, var(--sidebar)" }}>
        <Logo height={26} />
        <div className="max-w-md">
          <h2 className="text-[1.5rem] leading-[1.15] font-semibold tracking-[-0.015em]">One record from formulation to delivery.</h2>
          <p className="mt-4 text-[0.8125rem] leading-6 text-white/70">Purchasing, stores, production, quality, dispatch and accounts share a single, traceable source of truth.</p>
        </div>
        <p className="text-[0.75rem] text-white/50">Mintree Factory OS</p>
      </div>
      <div className="grid place-items-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm" noValidate>
          <h1 className="text-[1.5rem] font-semibold tracking-[-0.01em]">Sign in</h1>
          <p className="text-muted mt-1 mb-6 text-[0.8125rem]">Use your Mintree account.</p>
          {error && <div role="alert" className="mb-4 px-3 py-2 rounded-md text-[0.8125rem] text-danger" style={{ background: "color-mix(in srgb, var(--danger) 10%, transparent)" }}>{error}</div>}
          <label htmlFor="email" className="field-label">Email</label>
          <input id="email" className="input mb-3.5 !h-10" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="password" className="field-label">Password</label>
          <input id="password" className="input mb-5 !h-10" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn btn-primary w-full !h-10" disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    </div>
  );
}

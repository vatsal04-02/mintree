"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/ui";

export default function ChangePasswordPage() {
  const { me, refresh, logout } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [cur, setCur] = useState(""); const [next, setNext] = useState(""); const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const forced = me?.must_change_password;

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(null);
    if (next !== again) { setError("New passwords do not match"); return; }
    setBusy(true);
    try {
      await api("/auth/change-password", { body: { current_password: cur, new_password: next } });
      await refresh(); toast("success", "Password updated"); router.replace("/");
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not change password"); } finally { setBusy(false); }
  }

  return (
    <div className="min-h-screen grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm panel p-6" noValidate>
        <h1 className="text-[20px] font-semibold">{forced ? "Set a new password" : "Change password"}</h1>
        <p className="text-muted text-[13px] mt-1 mb-5">{forced ? "Your password was set by an administrator. Choose your own to continue." : "Other sessions will be signed out."}</p>
        {error && <div role="alert" className="mb-4 px-3 py-2 rounded-md text-[13px] text-danger" style={{ background: "color-mix(in srgb, var(--danger) 10%, transparent)" }}>{error}</div>}
        <label htmlFor="cur" className="field-label">Current password</label>
        <input id="cur" className="input mb-3" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        <label htmlFor="new" className="field-label">New password</label>
        <input id="new" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <p className="text-muted text-[11.5px] mt-1 mb-3">At least 10 characters with upper and lower case letters and a digit.</p>
        <label htmlFor="again" className="field-label">Confirm new password</label>
        <input id="again" className="input mb-5" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        <div className="flex gap-2">
          <button className="btn btn-primary flex-1" disabled={busy || !cur || !next || !again}>{busy ? "Saving…" : "Update password"}</button>
          {forced ? <button type="button" className="btn" onClick={logout}>Sign out</button> : <button type="button" className="btn" onClick={() => router.back()}>Cancel</button>}
        </div>
      </form>
    </div>
  );
}

"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Plus, Send, Sparkles } from "lucide-react";
import { api, ApiError, sharedGet } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtNum } from "@/lib/format";
import { Badge, Drawer } from "./ui";

type Cite = { label: string; href: string };
type Msg = { role: "user" | "assistant"; content: string; citations?: Cite[]; unverified?: string[]; failed?: boolean };
type Status = { enabled: boolean; demo: boolean; provider: string; can_assist: boolean; daily_token_budget: number; tokens_used_today: number };
type Section = { key: string; title: string; count: number; headline?: string; href?: string; items: { label: string; detail: string; href: string }[] };

const SUGGESTIONS = ["What is overdue?", "Which purchase orders are late?", "What is below reorder level?", "What needs attention in quality?"];

export function useAssistantStatus() {
  const { can } = useAuth();
  const [status, setStatus] = useState<Status | null>(null);
  useEffect(() => { if (can("ai.assistant:use")) sharedGet<Status>("/ai/status", undefined, 30_000).then(setStatus).catch(() => setStatus(null)); }, [can]);
  return status;
}

/** Read-only assistant. Everything shown comes from the API; the panel never computes figures. Citations are links built by the server from the records it read. */
export function AssistantPanel({ open, onClose, status }: { open: boolean; onClose: () => void; status: Status | null }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [conv, setConv] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState<Section[] | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && status?.enabled && brief === null) api<{ sections: Section[] }>("/ai/briefing").then((b) => setBrief(b.sections.filter((s) => s.count > 0))).catch(() => setBrief([]));
  }, [open, status?.enabled, brief]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);

  const send = useCallback(async (q: string) => {
    const message = q.trim();
    if (!message || busy) return;
    setBusy(true); setError(null); setText("");
    setMsgs((m) => [...m, { role: "user", content: message }]);
    try {
      const r = await api<{ conversation_id: number; answer: string; citations: Cite[]; unverified: string[]; failed: boolean }>("/ai/chat", { body: { message, conversation_id: conv } });
      setConv(r.conversation_id);
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, citations: r.citations, unverified: r.unverified, failed: r.failed }]);
    } catch (e) { setError(e instanceof ApiError ? e.message : "The assistant could not be reached."); }
    finally { setBusy(false); }
  }, [busy, conv]);

  const reset = () => { setMsgs([]); setConv(null); setError(null); };
  const left = status ? Math.max(0, status.daily_token_budget - status.tokens_used_today) : 0;

  return (
    <Drawer open={open} onClose={onClose} width={460} title="Assistant" subtitle="Reads your records. Cannot change anything."
      footer={status?.enabled ? (
        <form className="flex w-full items-end gap-2" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
          <label className="sr-only" htmlFor="ai-q">Ask a question</label>
          <textarea id="ai-q" className="input flex-1 !min-h-[40px]" rows={2} maxLength={4000} placeholder="Ask about stock, orders, receivables, quality…" value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }} disabled={busy} />
          <button className="btn btn-primary" disabled={busy || !text.trim()} aria-label="Send"><Send size={14} /></button>
        </form>
      ) : undefined}>
      {!status?.enabled ? (
        <div className="text-[13px] text-muted py-8 text-center" data-testid="ai-off">The assistant is switched off for this system. An administrator can enable it in the server settings.</div>
      ) : (
        <div className="flex flex-col gap-4" data-testid="ai-panel">
          <div className="flex items-center gap-2 text-[11.5px] text-muted">
            {status.demo && <Badge tone="warning">Demo provider</Badge>}
            <span>Allowance left today: {fmtNum(left)} tokens</span>
            {msgs.length > 0 && <button className="btn btn-ghost btn-sm ml-auto" onClick={reset}><Plus size={13} />New</button>}
          </div>
          {msgs.length === 0 && (
            <>
              {brief && brief.length > 0 && (
                <section aria-label="Today's briefing" className="panel p-3">
                  <h3 className="t-section mb-0.5">Today</h3>
                  <p className="text-[11.5px] text-muted mb-2">Built from your records; no AI model involved.</p>
                  <ul className="space-y-2">
                    {brief.map((s) => (
                      <li key={s.key} className="text-[12.5px]">
                        <div className="flex items-baseline gap-2"><span className="font-medium">{s.title}</span><span className="text-muted">{fmtNum(s.count)}</span></div>
                        {s.headline && <div className="text-muted">{s.headline}</div>}
                        {s.items.slice(0, 3).map((i) => <div key={i.label}><Link className="font-mono text-[12px] underline-offset-2 hover:underline" href={i.href} onClick={onClose}>{i.label}</Link> <span className="text-muted">{i.detail}</span></div>)}
                        {s.href && <Link className="text-[12px] underline-offset-2 hover:underline" href={s.href} onClick={onClose}>Review</Link>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <div className="flex flex-wrap gap-2">{SUGGESTIONS.map((s) => <button key={s} className="btn btn-sm" onClick={() => void send(s)}>{s}</button>)}</div>
            </>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? "self-end max-w-[85%] rounded-lg px-3 py-2 text-[13px] bg-surface-2" : "text-[13px] leading-relaxed"} data-role={m.role}>
              <div className="whitespace-pre-wrap">{m.content}</div>
              {!!m.unverified?.length && (
                <div className="mt-2 flex gap-2 rounded-md border border-line p-2 text-[12px]" role="note" data-testid="ai-unverified"><AlertTriangle size={14} className="shrink-0 mt-px text-warning" aria-hidden />
                  <span>These figures were not found in the records the assistant looked at: <b className="font-mono">{m.unverified.join(", ")}</b>. Do not rely on them.</span></div>
              )}
              {!!m.citations?.length && <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Sources">{m.citations.map((c) => <Link key={c.href + c.label} href={c.href} onClick={onClose} className="badge tone-neutral font-mono hover:underline">{c.label}</Link>)}</div>}
            </div>
          ))}
          {busy && <div className="text-[12.5px] text-muted" aria-live="polite"><Sparkles size={13} className="inline mr-1" />Looking at your records…</div>}
          {error && <div className="text-danger text-[12.5px]" role="alert">{error}</div>}
          <div ref={end} />
        </div>
      )}
    </Drawer>
  );
}

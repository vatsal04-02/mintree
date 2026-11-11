"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Camera, CameraOff, ScanLine } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Badge, Drawer, useToast } from "./ui";

export type Match = { type: string; type_label: string; id: number; label: string; status: string; link: string };
export type Resolved = { code: string; matches: Match[]; format: string };
export const resolveCode = (code: string) => api<Resolved>("/scan/resolve", { params: { code } });

const FORMATS = ["code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "qr_code", "data_matrix", "itf"];

/** Camera scanning. Uses the browser's native BarcodeDetector where it exists (Chrome/Edge/Android); otherwise ZXing (loaded on demand). The camera needs HTTPS or localhost. */
function CameraScanner({ onCode, active }: { onCode: (code: string) => void; active: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState<string>("");
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let stream: MediaStream | null = null;
    let controls: { stop: () => void } | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let last = "", lastAt = 0;
    const emit = (v: string) => { const now = Date.now(); if (v === last && now - lastAt < 2500) return; last = v; lastAt = now; onCode(v); };
    (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access needs HTTPS (or localhost) and a supported browser.");
        const BD = (window as any).BarcodeDetector;
        if (BD) {
          setEngine("native detector");
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
          if (stopped || !video.current) return;
          video.current.srcObject = stream; await video.current.play();
          const supported: string[] = await BD.getSupportedFormats?.().catch(() => FORMATS) ?? FORMATS;
          const det = new BD({ formats: FORMATS.filter((f) => supported.includes(f)) });
          timer = setInterval(async () => {
            if (!video.current || video.current.readyState < 2) return;
            try { const r = await det.detect(video.current); if (r[0]?.rawValue) emit(r[0].rawValue); } catch { /* frame not ready */ }
          }, 250);
        } else {
          setEngine("ZXing");
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          const reader = new BrowserMultiFormatReader();
          controls = await reader.decodeFromConstraints({ video: { facingMode: { ideal: "environment" } } }, video.current!, (res) => { if (res) emit(res.getText()); });
        }
      } catch (e) { setError(e instanceof Error ? e.message : "Could not start the camera"); }
    })();
    return () => { stopped = true; if (timer) clearInterval(timer); controls?.stop(); stream?.getTracks().forEach((t) => t.stop()); };
  }, [active, onCode]);
  if (error) return <div className="rounded-lg border border-line p-4 text-[0.75rem] flex gap-2.5 items-start"><CameraOff size={16} className="mt-0.5 shrink-0 text-muted" /><span>{error} You can still type or scan with a handheld scanner below.</span></div>;
  return (
    <div className="relative rounded-lg overflow-hidden bg-black aspect-[4/3]">
      <video ref={video} className="w-full h-full object-cover" playsInline muted />
      <div className="absolute inset-x-6 top-1/2 h-px bg-red-500/80 shadow-[0_0_8px_2px_rgba(239,68,68,.6)]" aria-hidden />
      <span className="absolute left-2 bottom-2 text-[0.75rem] text-white/80 bg-black/50 rounded px-1.5 py-0.5">{engine || "starting camera…"}</span>
    </div>
  );
}

/** The scan dialog: camera + a text box that also receives keyboard-wedge scanners (they "type" the code and press Enter). */
export function ScanDialog({ open, onClose, onCode, title = "Scan a barcode", hint, top, children }: { open: boolean; onClose: () => void; onCode: (code: string) => void | Promise<void>; title?: string; hint?: string; top?: React.ReactNode; children?: React.ReactNode }) {
  const [cam, setCam] = useState(false);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setText(""); setCam(false); setTimeout(() => input.current?.focus(), 50); } }, [open]);
  const submit = (e?: React.FormEvent) => { e?.preventDefault(); const v = text.trim(); if (v) { setText(""); onCode(v); } };
  const fromCamera = useCallback((v: string) => { onCode(v); }, [onCode]);
  return (
    <Drawer open={open} onClose={onClose} title={title} subtitle={hint ?? "Point the camera at a label, or scan with a handheld scanner, or type the code."} width={460}>
      {top}
      <form onSubmit={submit} className="flex gap-2 mb-4">
        <input ref={input} className="input flex-1 font-mono" placeholder="Scan or type a code, then Enter" value={text} onChange={(e) => setText(e.target.value)} aria-label="Barcode" autoComplete="off" />
        <button className="btn btn-primary" type="submit">Go</button>
      </form>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[0.75rem] text-muted">Camera</span>
        <button type="button" className="btn btn-sm" onClick={() => setCam((c) => !c)}><Camera size={13} />{cam ? "Stop camera" : "Use camera"}</button>
      </div>
      {cam && <CameraScanner active={cam && open} onCode={fromCamera} />}
      {children && <div className="mt-4">{children}</div>}
    </Drawer>
  );
}

/** Keyboard-wedge scanners type a whole code within a few ms and finish with Enter. When no field has focus, treat such a burst as a scan. */
export function useWedgeScanner(onScan: (code: string) => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    let buf = "", last = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const now = performance.now();
      if (now - last > 80) buf = "";
      last = now;
      if (e.key === "Enter") { if (buf.length >= 4) { e.preventDefault(); onScan(buf); } buf = ""; return; }
      if (e.key.length === 1) buf += e.key;
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onScan, enabled]);
}

/** Global scan: resolve any code to the record(s) it identifies and go there; if several records share the code, let the person choose. */
export function useGlobalScan() {
  const router = useRouter();
  const toast = useToast();
  const [choices, setChoices] = useState<Resolved | null>(null);
  const handle = useCallback(async (code: string) => {
    try {
      const r = await resolveCode(code);
      if (r.matches.length === 0) { toast("error", `“${code}” does not match anything you can access`); return; }
      if (r.matches.length === 1) { router.push(r.matches[0].link); return; }
      setChoices(r);
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not read that code"); }
  }, [router, toast]);
  const dialog = (
    <Drawer open={!!choices} onClose={() => setChoices(null)} title="Several records match" subtitle={choices?.code} width={440}>
      <ul className="divide-y divide-line panel">
        {choices?.matches.map((m) => (
          <li key={`${m.type}-${m.id}`}><button className="w-full text-left px-3.5 py-3 hover:bg-surface-2 flex items-center justify-between gap-3" onClick={() => { setChoices(null); router.push(m.link); }}>
            <span><span className="text-muted text-[0.75rem] block">{m.type_label}</span><span className="font-medium">{m.label}</span></span><Badge>{m.status}</Badge></button></li>
        ))}
      </ul>
    </Drawer>
  );
  return { handle, dialog };
}

export function ScanButton({ onClick, label = "Scan" }: { onClick: () => void; label?: string }) {
  return <button type="button" onClick={onClick} className="btn btn-sm" aria-label="Scan a barcode"><ScanLine size={14} />{label}</button>;
}

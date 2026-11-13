"use client";
/** Small per-user preferences kept in this browser (pinned actions, favourites, recent pages, saved table views). They are conveniences, never data:
 *  the page works without them, and reading or writing storage can fail (private window, blocked site data), so every access is guarded.
 *  The key includes the signed-in user, so two people sharing a browser do not see each other's pins. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./auth";

export function usePref<T>(name: string, initial: T): [T, (next: T | ((prev: T) => T)) => void] {
  const { me } = useAuth();
  const key = `mintree:${me?.id ?? "anon"}:${name}`;
  const [value, setValue] = useState<T>(initial);
  const ref = useRef<T>(initial);
  const fallback = useRef(initial);

  useEffect(() => {
    let v = fallback.current;
    try { const raw = localStorage.getItem(key); if (raw) v = JSON.parse(raw) as T; } catch { /* storage unavailable: keep the default */ }
    ref.current = v; setValue(v);
    const sync = (e: Event) => { if ((e as CustomEvent<string>).detail !== key) return; try { const raw = localStorage.getItem(key); if (raw) { ref.current = JSON.parse(raw) as T; setValue(ref.current); } } catch { /* ignore */ } };
    window.addEventListener("mintree-pref", sync);
    return () => window.removeEventListener("mintree-pref", sync);
  }, [key]);

  const set = useCallback((next: T | ((prev: T) => T)) => {
    const v = typeof next === "function" ? (next as (p: T) => T)(ref.current) : next;
    ref.current = v; setValue(v);
    try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ }
    window.dispatchEvent(new CustomEvent("mintree-pref", { detail: key }));
  }, [key]);

  return [value, set];
}

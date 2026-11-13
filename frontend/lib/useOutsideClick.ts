"use client";
import { useEffect } from "react";

/** Calls `fn` when the pointer goes down outside `ref`, or Escape is pressed, while `on` is true. */
export function useOutsideClick(ref: React.RefObject<HTMLElement | null>, fn: () => void, on: boolean) {
  useEffect(() => {
    if (!on) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) fn(); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") fn(); };
    document.addEventListener("mousedown", h); document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [on, fn, ref]);
}

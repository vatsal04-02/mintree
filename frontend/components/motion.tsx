"use client";
/** Shared motion for the Rapport language. One easing, one spring, one stagger, so every page and card arrives the same way.
 *  Everything here is switched off for people who ask for reduced motion, and animates transform, opacity and blur only (no layout shift).
 *    PageTransition  wraps a routed page; it re-keys on the path so each navigation plays the entrance once.
 *    Reveal          a card that arrives as a pale ghost, then fills (fade, rise 14px, de-blur), staggered by `index` at 55ms.
 *    SlideMarker     the sliding active marker shared by tab groups and the sidebar (Framer shared layout).
 *  Plain CSS pages get the same entrance from the `.rise` and `.page-in` classes in globals.css, which use the same numbers. */
import { motion, useReducedMotion } from "framer-motion";
import { usePathname } from "next/navigation";

export const EASE = [0.22, 1, 0.36, 1] as const;           // cubic-bezier(.22, 1, .36, 1): quick start, soft landing
export const SPRING = { type: "spring", stiffness: 380, damping: 32 } as const;
export const STAGGER = 0.055;                               // seconds between sibling cards
export const ENTER = 0.45;                                  // seconds for a card to arrive

/** A routed page. The content fades in and de-blurs once per navigation. The previous page cannot be kept on screen in the App Router, so there is
 *  no exit animation: the new page simply arrives. */
export function PageTransition({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const path = usePathname();
  return <div key={path} className={`page-in ${className}`}>{children}</div>;
}

/** A card that appears as a ghost placeholder (the `ghost` layer, a pale tile of the card's own shape), then fills with its content. */
export function Reveal({ children, index = 0, className = "", radius = 28 }: { children: React.ReactNode; index?: number; className?: string; radius?: number }) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  const delay = index * STAGGER;
  return (
    <div className={`relative ${className}`}>
      <motion.span aria-hidden className="absolute inset-0 pointer-events-none ghost-tile" style={{ borderRadius: radius }}
        initial={{ opacity: 1 }} animate={{ opacity: 0 }} transition={{ duration: 0.3, delay: delay + 0.18, ease: EASE }} />
      <motion.div initial={{ opacity: 0, y: 14, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: ENTER, delay, ease: EASE }}>
        {children}
      </motion.div>
    </div>
  );
}

/** The active-tab marker. Render it inside the active item (which must be `relative`); with the same `id` it glides between items instead of jumping. */
export function SlideMarker({ id, className = "", style }: { id: string; className?: string; style?: React.CSSProperties }) {
  const reduce = useReducedMotion();
  return <motion.span aria-hidden layoutId={id} className={`absolute pointer-events-none ${className}`} style={style} transition={reduce ? { duration: 0 } : SPRING} />;
}

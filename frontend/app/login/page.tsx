"use client";
/** Sign-in over the Mintree range: the product shelf sits behind the page at low opacity, a soft spotlight follows the pointer and shows the
 *  products in full colour, the shelf drifts a little with the pointer, and small hotspots name each product. Motion is skipped for people who
 *  ask for reduced motion, and on touch screens the shelf simply rests at low opacity. */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useMotionTemplate, useMotionValue, useReducedMotion, useSpring, useTransform } from "framer-motion";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Logo } from "@/components/Logo";

const SHELF = "/brand/products.jpg";
/** Hotspots in percent of the shelf photo (1270 x 650). */
const PRODUCTS = [
  { name: "Oud-One-Out Body Wash", note: "750 ml recharge refill", x: 16, y: 60 },
  { name: "Estonian Peat Detan Face Mask", note: "Refill pouch and jar", x: 46, y: 30 },
  { name: "Peat Face Mist", note: "Recharge refill", x: 55, y: 72 },
  { name: "Fall Stop Shampoo", note: "750 ml recharge refill", x: 74, y: 42 },
  { name: "Oud-One-Out dispenser", note: "Refillable wooden bottle", x: 30, y: 72 },
];

export default function LoginPage() {
  const { login, me, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [fine, setFine] = useState(false);
  const reduce = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const shelf = useRef<HTMLDivElement>(null);

  // pointer position in px for the spotlight, and -0.5..0.5 for the parallax drift
  const mx = useMotionValue(-999), my = useMotionValue(-999);
  const px = useMotionValue(0), py = useMotionValue(0);
  const sx = useSpring(px, { stiffness: 60, damping: 18 }), sy = useSpring(py, { stiffness: 60, damping: 18 });
  const tx = useTransform(sx, (v) => v * -26), ty = useTransform(sy, (v) => v * -18);
  const mask = useMotionTemplate`radial-gradient(230px 230px at ${mx}px ${my}px, #000 0%, rgb(0 0 0 / 0.7) 40%, transparent 100%)`;

  useEffect(() => { if (!loading && me) router.replace(me.must_change_password ? "/change-password" : "/"); }, [me, loading, router]);
  useEffect(() => { setFine(window.matchMedia("(pointer: fine)").matches); }, []);

  function move(e: React.PointerEvent) {
    if (!fine) return;
    const r = root.current?.getBoundingClientRect();
    if (!r) return;
    // the colour copy is masked in its own coordinates, which move with the drift and the cover sizing
    const b = shelf.current?.getBoundingClientRect();
    if (b) { mx.set(e.clientX - b.left); my.set(e.clientY - b.top); }
    if (!reduce) { px.set((e.clientX - r.left) / r.width - 0.5); py.set((e.clientY - r.top) / r.height - 0.5); }
  }
  function leave() { mx.set(-999); my.set(-999); px.set(0); py.set(0); }

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { const m = await login(email.trim(), password); router.replace(m.must_change_password ? "/change-password" : "/"); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not sign in"); setBusy(false); }
  }

  /* The shelf is sized like background-size: cover, so the hotspots stay on their products at any window shape. */
  const shelfBox = "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[max(104vw,203vh)] aspect-[1270/650]";

  return (
    <div ref={root} onPointerMove={move} onPointerLeave={leave} className="relative min-h-screen overflow-hidden" style={{ background: "#efe5d8", color: "#151717" }}>
      {/* the shelf: a low-opacity base and a full-colour copy revealed under the pointer */}
      <motion.div className="absolute inset-0" style={reduce ? undefined : { x: tx, y: ty }} aria-hidden>
        <div ref={shelf} className={shelfBox}>
          <motion.img src={SHELF} alt="" className="absolute inset-0 w-full h-full object-cover select-none" draggable={false}
            initial={{ opacity: 0, scale: 1.04 }} animate={{ opacity: 0.26, scale: 1 }} transition={{ duration: 1.4, ease: [0.22, 1, 0.36, 1] }} style={{ filter: "saturate(0.85)" }} />
          {fine && <motion.img src={SHELF} alt="" className="absolute inset-0 w-full h-full object-cover select-none" draggable={false}
            style={{ opacity: 0.9, WebkitMaskImage: mask, maskImage: mask }} />}
        </div>
      </motion.div>

      {/* warm wash so the type always reads, strongest behind the sign-in card */}
      <div className="absolute inset-0 pointer-events-none" aria-hidden style={{ background: "linear-gradient(90deg, rgb(239 229 216 / 0.15) 0%, rgb(239 229 216 / 0) 40%, rgb(239 229 216 / 0.55) 100%), linear-gradient(180deg, rgb(239 229 216 / 0.7) 0%, transparent 22%, transparent 78%, rgb(239 229 216 / 0.8) 100%), radial-gradient(700px 420px at 0% 100%, rgb(239 229 216 / 0.85), transparent 70%)" }} />

      {/* product hotspots (pointer devices only; they sit under the card where they overlap it) */}
      {fine && (
        <motion.div className="absolute inset-0 hidden md:block" style={reduce ? undefined : { x: tx, y: ty }}>
          <div className={shelfBox}>
            {PRODUCTS.map((p, i) => (
              <button key={p.name} type="button" tabIndex={-1} aria-hidden onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}
                className="absolute -translate-x-1/2 -translate-y-1/2 group" style={{ left: `${p.x}%`, top: `${p.y}%` }}>
                <span className="relative grid place-items-center w-7 h-7">
                  <span className="absolute inset-0 rounded-full bg-white/50 animate-ping [animation-duration:2.6s]" />
                  <span className="relative w-3 h-3 rounded-full bg-white shadow-[0_0_0_4px_rgb(255_255_255/0.35)]" />
                </span>
                <motion.span initial={false} animate={active === i ? { opacity: 1, y: 0 } : { opacity: 0, y: 6 }} transition={{ duration: 0.22 }}
                  className="absolute left-1/2 -translate-x-1/2 bottom-9 whitespace-nowrap rounded-2xl px-3 py-2 text-left pointer-events-none"
                  style={{ background: "rgb(21 23 23 / 0.86)", color: "#fff", backdropFilter: "blur(8px)" }}>
                  <span className="block text-[0.8125rem] font-medium">{p.name}</span>
                  <span className="block text-[0.6875rem] text-white/70">{p.note}</span>
                </motion.span>
              </button>
            ))}
          </div>
        </motion.div>
      )}

      <div className="relative z-10 min-h-screen grid lg:grid-cols-[1fr_440px] gap-8 p-6 md:p-10 pointer-events-none">
        <div className="hidden lg:flex flex-col justify-between">
          <Logo height={30} />
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, delay: 0.25, ease: [0.22, 1, 0.36, 1] }} className="max-w-md">
            <h2 className="text-[2.5rem] leading-[1.05] font-normal tracking-[-0.03em]">From formulation<br />to refill pouch.</h2>
            <p className="mt-4 text-[0.9375rem] leading-6 text-[#151717]/70">Purchasing, stores, production, quality, dispatch and accounts on one traceable record, for every product on this shelf.</p>
            {fine && <p className="mt-6 inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[0.75rem]" style={{ background: "rgb(255 255 255 / 0.6)", backdropFilter: "blur(8px)" }}><span className="w-1.5 h-1.5 rounded-full" style={{ background: "#c6431d" }} />Move across the shelf to explore the range</p>}
          </motion.div>
        </div>

        <div className="grid place-items-center">
          <motion.form onSubmit={submit} noValidate initial={{ opacity: 0, y: 18, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
            className="pointer-events-auto w-full max-w-sm rounded-[28px] p-7 md:p-8"
            style={{ background: "rgb(255 255 255 / 0.62)", backdropFilter: "blur(22px) saturate(1.2)", WebkitBackdropFilter: "blur(22px) saturate(1.2)", boxShadow: "0 30px 80px -30px rgb(70 45 20 / 0.45), inset 0 0 0 1px rgb(255 255 255 / 0.6)" }}>
            <div className="lg:hidden mb-6"><Logo height={24} /></div>
            <h1 className="text-[1.75rem] leading-8 font-normal tracking-[-0.02em]">Sign in</h1>
            <p className="mt-1 mb-6 text-[0.8125rem] text-[#151717]/65">Use your Mintree account.</p>
            {error && <div role="alert" className="mb-4 px-3 py-2 rounded-2xl text-[0.8125rem]" style={{ background: "rgb(198 67 29 / 0.1)", color: "#a3361a" }}>{error}</div>}
            <label htmlFor="email" className="field-label">Email</label>
            <input id="email" className="input mb-3.5 !h-11 !rounded-full !px-4 !bg-white/80 !text-[#151717]" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
            <label htmlFor="password" className="field-label">Password</label>
            <input id="password" className="input mb-6 !h-11 !rounded-full !px-4 !bg-white/80 !text-[#151717]" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="btn btn-primary w-full !h-11" disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
            <p className="mt-5 text-center text-[0.6875rem] text-[#151717]/50">Mintree Factory OS · Factory Plant 01</p>
          </motion.form>
        </div>
      </div>
    </div>
  );
}

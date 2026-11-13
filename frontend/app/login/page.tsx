"use client";
/** Sign-in for Mintree Factory OS. A split screen: the left is the brand and the plant (deep Mintree teal, the real product shelf in full colour, and the chain
 *  of work every batch moves through); the right is a quiet, solid sign-in form. Nothing here is decorative data: the chain is the module list of the application. */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import { Boxes, ClipboardCheck, Factory, PackageCheck, Receipt, ShoppingCart } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Logo } from "@/components/Logo";

const SHELF = "/brand/products.jpg";
/** The work every batch passes through, in order. */
const CHAIN = [
  { label: "Purchase", icon: ShoppingCart },
  { label: "Stores", icon: Boxes },
  { label: "Production", icon: Factory },
  { label: "Quality", icon: ClipboardCheck },
  { label: "Dispatch", icon: PackageCheck },
  { label: "Accounts", icon: Receipt },
];
/** Three slices of the shelf photo (left, middle, right), each tilted a little and set at its own height. */
const CARDS = [
  { left: "0%", pos: 0, rot: -3.5, dy: 10, bob: -5, z: 1 },
  { left: "34.2%", pos: 50, rot: 2, dy: -4, bob: -7, z: 2 },
  { left: "68.5%", pos: 100, rot: -2.5, dy: 8, bob: -5, z: 1 },
] as const;
const EASE = [0.22, 1, 0.36, 1] as const;

export default function LoginPage() {
  const { login, me, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reduce = useReducedMotion();

  useEffect(() => { if (!loading && me) router.replace(me.must_change_password ? "/change-password" : "/"); }, [me, loading, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { const m = await login(email.trim(), password); router.replace(m.must_change_password ? "/change-password" : "/"); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not sign in"); setBusy(false); }
  }

  const rise = (delay: number) => (reduce ? {} : { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.6, delay, ease: EASE } });

  return (
    <div className="min-h-screen grid lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]" style={{ background: "#f6f1e8", color: "#151717" }}>
      {/* ---- brand and plant ---- */}
      <section className="relative overflow-hidden text-white px-6 py-7 sm:px-10 lg:px-14 lg:py-12 flex flex-col" aria-label="About Mintree Factory OS"
        style={{ background: "linear-gradient(160deg, #015a52 0%, #024840 62%, #03362f 100%)" }}>
        <div aria-hidden className="absolute inset-0 opacity-[0.07] pointer-events-none" style={{ backgroundImage: "linear-gradient(rgb(255 255 255) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255) 1px, transparent 1px)", backgroundSize: "44px 44px" }} />
        <div aria-hidden className="absolute -top-40 -right-40 w-[28rem] h-[28rem] rounded-full pointer-events-none" style={{ background: "radial-gradient(circle, rgb(143 230 216 / 0.16), transparent 65%)" }} />

        <motion.div {...rise(0)} className="relative flex items-center gap-3">
          <Logo height={30} color="#ffffff" />
          <span className="h-5 w-px bg-white/30" aria-hidden />
          <span className="text-[0.8125rem] tracking-wide text-white/80">Factory OS</span>
        </motion.div>

        <div className="relative mt-10 lg:mt-auto max-w-xl">
          <motion.h2 {...rise(0.18)} className="mt-3 text-[2rem] sm:text-[2.5rem] lg:text-[2.875rem] leading-[1.06] font-normal tracking-[-0.03em]">
            Every batch, traceable<br className="hidden sm:block" /> from lot to customer.
          </motion.h2>
        </div>

        <motion.figure {...rise(0.34)} className="relative mt-8 hidden md:block" aria-label="The Mintree range: refill pouches, a refillable dispenser, a face mask jar and a face mist">
          {/* the shelf photo, cut into three cards that float at slight angles; together they show the whole picture */}
          <div className="relative h-44 lg:h-52" role="img" aria-label="The Mintree range: refill pouches, a refillable dispenser, a face mask jar and a face mist">
            {CARDS.map((c, i) => (
              <motion.div key={i} className="absolute top-0 bottom-0 rounded-[22px] ring-1 ring-white/25 shadow-[0_26px_44px_-22px_rgb(0_0_0/0.7)]"
                style={{ left: c.left, width: "31.5%", rotate: c.rot, zIndex: c.z, backgroundImage: `url(${SHELF})`, backgroundSize: "317% auto", backgroundPosition: `${c.pos}% 46%`, marginTop: c.dy, marginBottom: -c.dy }}
                animate={reduce ? undefined : { y: [0, c.bob, 0] }} transition={{ duration: 5 + i, repeat: Infinity, ease: "easeInOut", delay: i * 0.6 }}>
                <span aria-hidden className="absolute inset-0 rounded-[22px]" style={{ background: "linear-gradient(180deg, rgb(255 255 255 / 0.12), transparent 40%, rgb(3 54 47 / 0.28))" }} />
              </motion.div>
            ))}
          </div>
        </motion.figure>

        <motion.ol {...rise(0.42)} className="relative mt-7 grid grid-cols-3 sm:grid-cols-6 gap-x-2 gap-y-5" aria-label="How work moves through the plant">
          {CHAIN.map((c, i) => {
            const Icon = c.icon;
            return (
              <li key={c.label} className="relative flex flex-col items-start gap-2 min-w-0">
                {i < CHAIN.length - 1 && <span aria-hidden className="hidden sm:block absolute top-[17px] left-10 right-[-0.5rem] h-px bg-gradient-to-r from-white/40 to-white/10" />}
                <span className="relative grid place-items-center w-[34px] h-[34px] rounded-full bg-white/10 ring-1 ring-white/30"><Icon size={16} strokeWidth={1.5} aria-hidden /></span>
                <span className="text-[0.8125rem] font-medium leading-4">{c.label}</span>
              </li>
            );
          })}
        </motion.ol>
      </section>

      {/* ---- sign in ---- */}
      <main className="flex items-center justify-center px-6 py-10 sm:px-10">
        <motion.form onSubmit={submit} noValidate {...rise(0.15)} className="w-full max-w-[22rem]" aria-labelledby="signin-title">
          <h1 id="signin-title" className="text-[1.875rem] leading-9 font-normal tracking-[-0.02em]">Sign in</h1>
          <div className="mb-7" />
          {error && <div role="alert" className="mb-4 px-3.5 py-2.5 rounded-2xl text-[0.8125rem]" style={{ background: "rgb(198 67 29 / 0.1)", color: "#a3361a" }}>{error}</div>}
          <label htmlFor="email" className="field-label">Email</label>
          <input id="email" className="input mb-4 !h-11 !rounded-xl !px-4 !bg-white !text-[#151717]" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="password" className="field-label">Password</label>
          <input id="password" className="input mb-6 !h-11 !rounded-xl !px-4 !bg-white !text-[#151717]" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn btn-primary w-full !h-11 !rounded-xl" disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
          <p className="mt-8 text-[0.6875rem] text-[#151717]/45">Factory Plant 01</p>
        </motion.form>
      </main>
    </div>
  );
}

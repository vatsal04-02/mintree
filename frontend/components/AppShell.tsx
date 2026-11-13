"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScanLine, MessageSquareText, Bell as BellIcon, Menu, ChevronDown, ChevronRight, KeyRound, LogOut, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Sun , Monitor, Star, CornerDownLeft, type LucideIcon } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { personaFor } from "@/lib/persona";
import { PageTransition, SlideMarker } from "./motion";
import { Logo } from "./Logo";
import { ALL_NAV_ITEMS, NAV, visible } from "@/lib/nav";
import { api, type Page } from "@/lib/api";
import { useDebounced, useToast } from "./ui";
import { usePref } from "@/lib/prefs";
import { ACTIONS, runShell } from "@/lib/actions";
import { MobileBar, ShortcutSheet } from "./ShellExtras";
import { resolveCode } from "./Scanner";
import { ScanDialog, useGlobalScan, useWedgeScanner } from "./Scanner";
import { AssistantPanel, useAssistantStatus } from "./AssistantPanel";
import { DemoPill } from "./DemoControls";
import { LiveIndicator } from "./LiveIndicator";
import { useLiveEvent } from "@/lib/live";

/** Appearance follows the system (and its changes) until the person picks one in the menu; only an explicit pick is stored. "Match system" removes it again. */
function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => (typeof document !== "undefined" && document.documentElement.dataset.theme === "dark" ? "dark" : "light"));   // set before paint by the script in the layout
  const [custom, setCustom] = useState(false);
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const sync = () => { let s: string | null = null; try { s = localStorage.getItem("mintree-theme"); } catch {} setCustom(s === "dark" || s === "light"); setTheme(s === "dark" || s === "light" ? s : mq.matches ? "dark" : "light"); };
    sync(); mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const toggle = () => setTheme((t) => { const n = t === "dark" ? "light" : "dark"; try { localStorage.setItem("mintree-theme", n); } catch {} setCustom(true); return n; });
  const useSystem = () => { try { localStorage.removeItem("mintree-theme"); } catch {} setCustom(false); setTheme(matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); };
  return [theme, toggle, custom, useSystem] as const;
}

function useOutside(ref: React.RefObject<HTMLElement | null>, fn: () => void, on: boolean) {
  useEffect(() => {
    if (!on) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) fn(); };
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") fn(); };
    document.addEventListener("mousedown", h); document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [on, fn, ref]);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, can, logout } = useAuth();
  const persona = personaFor(me?.email);
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [closedGroups, setClosedGroups] = useState<Record<string, boolean>>({});
  const [mobileOpen, setMobileOpen] = useState(false);
  useEffect(() => { setMobileOpen(false); setExpanded({}); }, [pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setMobileOpen(false); };
    document.addEventListener("keydown", k); return () => document.removeEventListener("keydown", k);
  }, [mobileOpen]);
  const toggleGroup = (href: string, open: boolean) => setExpanded(Object.fromEntries(NAV.flatMap((g) => g.items).filter((i) => i.children).map((i) => [i.href, i.href === href ? !open : false])));
  const [theme, toggleTheme, themeCustom, matchSystemTheme] = useTheme();
  const [assist, setAssist] = useState(false);
  const aiStatus = useAssistantStatus();
  const toggleAssist = useCallback(() => setAssist((v) => !v), []);
  useEffect(() => { try { setCollapsed(localStorage.getItem("mintree-nav") === "1"); } catch {} }, []);
  const toggleNav = () => setCollapsed((c) => { try { localStorage.setItem("mintree-nav", c ? "0" : "1"); } catch {} return !c; });
  const [shortcuts, setShortcuts] = useState(false);
  const [favs, setFavs] = usePref<string[]>("favs", []);
  const [, setRecent] = usePref<string[]>("recent", []);   // still recorded: the command palette lists recent pages
  const [scrolledTitle, setScrolledTitle] = useState<string | null>(null);
  useEffect(() => {
    /* "?" opens the shortcut sheet; "g" then o, a or n jumps to Overview, Approvals or Notifications. Ignored while typing. */
    let g = 0;
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable=true], [role=dialog]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "?") { e.preventDefault(); setShortcuts(true); return; }
      const key = e.key.toLowerCase();
      if (key === "g") { g = Date.now(); return; }
      if (g && Date.now() - g < 1200) { g = 0; const to = ({ o: "/", a: "/approvals", n: "/notifications" } as Record<string, string>)[key]; if (to) { e.preventDefault(); router.push(to); } }
    };
    const open = () => setShortcuts(true);
    document.addEventListener("keydown", k); window.addEventListener("mintree:shortcuts", open);
    return () => { document.removeEventListener("keydown", k); window.removeEventListener("mintree:shortcuts", open); };
  }, [router]);
  useEffect(() => {
    /* the page title moves into the top bar once the page has scrolled past it */
    const el = document.getElementById("main");
    if (!el) return;
    const on = () => setScrolledTitle(el.scrollTop > 72 ? el.querySelector("h1")?.textContent?.trim() || null : null);
    el.addEventListener("scroll", on, { passive: true }); on();
    return () => el.removeEventListener("scroll", on);
  }, [pathname]);

  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => visible(i, can)) })).filter((g) => g.items.length);
  const current = [...ALL_NAV_ITEMS].filter((i) => (i.href === "/" ? pathname === "/" : pathname.startsWith(i.href))).sort((a, b) => b.href.length - a.href.length)[0] ?? ALL_NAV_ITEMS.flatMap((i) => (i.children ?? []).map((c) => ({ ...c, label: c.label }))).filter((c) => c.href !== "/" && pathname.startsWith(c.href)).sort((a, b) => b.href.length - a.href.length)[0];

  const here = current?.href;
  useEffect(() => { if (here && here !== "/") setRecent((r) => (r[0] === here ? r : [here, ...r.filter((x) => x !== here)].slice(0, 6))); }, [here, setRecent]);
  const byHref = (h: string) => ALL_NAV_ITEMS.find((i) => i.href === h && visible(i, can));
  const favItems = favs.map(byHref).filter((x): x is NonNullable<typeof x> => !!x);
  const parent = here ? NAV.flatMap((g) => g.items).find((i) => i.children?.some((c) => c.href === here)) : undefined;
  const siblings = parent?.children?.filter((c) => !c.perm || can(c.perm)) ?? [];
  const isFav = !!here && favs.includes(here);
  const toggleFav = () => { if (here) setFavs((f) => (f.includes(here) ? f.filter((x) => x !== here) : [here, ...f].slice(0, 8))); };

  return (
    <div className="flex h-screen overflow-hidden md:p-3 md:gap-3" style={{ background: "var(--frame)" }}>
      <a href="#main" className="skip-link">Skip to content</a>
      {mobileOpen && <div className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setMobileOpen(false)} aria-hidden />}
      <aside id="primary-nav" className={`${collapsed ? "md:w-[60px]" : "md:w-[244px]"} w-[260px] shrink-0 flex flex-col transition-transform md:transition-[width] duration-150 fixed inset-y-0 left-0 z-40 md:static md:rounded-3xl overflow-hidden ${mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}`} style={{ background: "var(--sidebar)", color: "var(--sidebar-fg)" }}>
        <div className="h-14 flex items-center px-4 shrink-0 border-b border-white/[0.12]">
          <Link href="/" aria-label="Mintree Factory OS, overview" className="flex items-center gap-2.5 min-w-0 min-h-7">
            {collapsed ? <Logo mark height={22} color="#ffffff" /> : <><Logo height={20} color="#ffffff" /><span className="text-[0.75rem] font-medium tracking-wide text-white/80 border-l border-white/20 pl-2.5 leading-none py-0.5">Factory OS</span></>}
          </Link>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-8 sidebar-scroll [mask-image:linear-gradient(to_bottom,black_calc(100%-28px),transparent)]" aria-label="Main">
          {favItems.length > 0 && (
            <div className="mt-1 mb-1" aria-label="Pinned pages">
              {([["Pinned", favItems]] as const).map(([title, list]) => list.length > 0 && (
                <div key={title} className="mb-3">
                  {!collapsed ? <p className="px-2.5 mb-1 min-h-6 flex items-center text-[0.75rem] font-medium" style={{ color: "var(--sidebar-label)" }}>{title}</p> : <div className="mx-2.5 mb-1.5 border-t border-white/10" aria-hidden />}
                  {list.map((i) => { const Ic = i.icon; const on = i.href === here; return (
                    <Link key={i.href} href={i.href} title={collapsed ? i.label : undefined} aria-current={on ? "page" : undefined}
                      className={`flex items-center gap-2.5 h-9 px-3 rounded-full text-[0.8125rem] mb-px transition-colors ${on ? "font-medium bg-white/[0.16] text-white" : "text-white/85 hover:bg-white/[0.06]"}`}>
                      <Ic size={collapsed ? 18 : 16} strokeWidth={1.75} className="shrink-0" aria-hidden />{!collapsed && <span className="truncate">{i.label}</span>}
                    </Link>); })}
                </div>
              ))}
            </div>
          )}
          {groups.map((g) => (
            <div key={g.label} className="mt-5 first:mt-1">
              {!collapsed
                ? <button type="button" aria-expanded={!closedGroups[g.label]} onClick={() => setClosedGroups((c) => ({ ...c, [g.label]: !c[g.label] }))}
                    style={{ color: "var(--sidebar-label)" }} className="group/label flex items-center w-full px-2.5 mb-1 min-h-7 text-[0.75rem] font-medium hover:text-white transition-colors duration-150">
                    <span className="flex-1 text-left">{g.label}</span><ChevronDown size={12} aria-hidden className={`opacity-0 group-hover/label:opacity-100 group-focus-visible/label:opacity-100 transition-[opacity,transform] duration-150 ${closedGroups[g.label] ? "-rotate-90" : ""}`} />
                  </button>
                : <div className="mx-2.5 mb-1.5 border-t border-white/10" aria-hidden />}
              {(collapsed || !closedGroups[g.label]) && g.items.map((i) => {
                const active = i === current || (i.href === "/" ? pathname === "/" : pathname.startsWith(i.href));
                const Icon = i.icon;
                if (i.children) {
                  const kids = i.children.filter((c) => !c.perm || can(c.perm));
                  const open = !collapsed && (expanded[i.href] ?? pathname.startsWith(i.href));
                  return (
                    <div key={i.href} className="mb-px">
                      <button type="button" aria-expanded={open} title={collapsed ? i.label : undefined}
                        onClick={() => (collapsed ? router.push(kids[0]?.href ?? i.href) : toggleGroup(i.href, open))}
                        className={`flex items-center gap-2.5 h-10 px-3 rounded-full text-[0.8125rem] w-full text-left transition-colors duration-150 ${active ? "text-white font-medium bg-white/[0.06]" : "hover:bg-white/[0.06]"}`}>
                        <Icon size={18} strokeWidth={1.75} className="shrink-0" />
                        {!collapsed && <><span className="truncate flex-1">{i.label}</span><ChevronRight size={13} aria-hidden className={`shrink-0 opacity-35 transition-transform duration-150 ${open ? "rotate-90 opacity-60" : ""}`} /></>}
                      </button>
                      {open && (
                        <div className="ml-[19px] pl-3 border-l border-white/[0.10] my-0.5">
                          {kids.map((c) => {
                            const here = c.href === `/m/${i.href.split("/")[2]}` ? pathname === c.href : pathname === c.href || pathname.startsWith(`${c.href}/`);
                            return (
                              <Link key={c.href + c.label} href={c.href} aria-current={here ? "page" : undefined}
                                className={`relative block h-8 leading-8 px-3 rounded-lg text-[0.75rem] truncate transition-colors ${here ? "text-white font-medium" : "text-white/85 hover:text-white hover:bg-white/[0.07]"}`}>{here && <SlideMarker id="nav-sub" className="left-0.5 top-1.5 bottom-1.5 w-[2px] rounded-full bg-[var(--brand-on-ink)]" />}{c.label}</Link>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                }
                return (
                  <Link key={i.href} href={i.href} title={collapsed ? i.label : undefined} aria-current={active ? "page" : undefined}
                    className={`relative isolate flex items-center gap-2.5 h-10 px-3 rounded-full text-[0.8125rem] mb-px transition-colors duration-150 ${active ? "font-medium" : "hover:bg-white/[0.06]"}`}
                    style={active ? { color: "#fff" } : undefined}>
                    {active && <SlideMarker id="nav-pill" className="inset-0 rounded-full -z-10" style={{ background: "rgb(255 255 255 / 0.16)" }} />}
                    {i.plain && !collapsed ? <span className="w-4 shrink-0" aria-hidden /> : <Icon size={18} strokeWidth={1.75} className="shrink-0" aria-hidden />}{!collapsed && <span className="truncate">{i.label}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="p-3 shrink-0">
          <div className={`flex items-center ${collapsed ? "justify-center p-1.5 rounded-full" : "gap-2.5 px-2.5 py-2.5 rounded-2xl"}`} style={{ background: "var(--ink-2)" }}>
            {persona
              // eslint-disable-next-line @next/next/no-img-element
              ? <img src={persona.photo} alt="" className="w-9 h-9 min-w-9 aspect-square rounded-full object-cover shrink-0 ring-2 ring-white/25" />
              : <div className="w-8 h-8 min-w-8 aspect-square rounded-full grid place-items-center text-[0.75rem] font-semibold shrink-0" style={{ background: "var(--sidebar-active)", color: "#fff" }} aria-hidden>
                  {me?.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase()}
                </div>}
            {!collapsed && <div className="min-w-0 leading-tight flex-1"><div className="text-white text-[0.8125rem] truncate">{persona?.name ?? me?.full_name}</div><div className="text-[0.75rem] text-white/80 truncate">{persona?.title ?? `${me?.roles[0] ?? "No role"} · Mintree`}</div></div>}
          </div>
          <button onClick={toggleNav} className="hidden md:flex items-center gap-2.5 h-9 px-3 mt-1 rounded-full w-full text-[0.75rem] hover:bg-white/[0.06]" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <PanelLeftOpen size={16} /> : <><PanelLeftClose size={16} />Collapse</>}
          </button>
        </div>
      </aside>

      <div className="relative flex-1 min-w-0 flex flex-col canvas md:rounded-3xl overflow-hidden">
        <Header current={scrolledTitle ?? current?.label} parentLabel={parent?.label} siblings={siblings} here={here} isFav={isFav} onFav={toggleFav} theme={theme} toggleTheme={toggleTheme} themeCustom={themeCustom} matchSystemTheme={matchSystemTheme} logout={logout} onMenu={() => { setCollapsed(false); setMobileOpen(true); }} menuOpen={mobileOpen} aiStatus={aiStatus} onAssist={toggleAssist} />
        <main id="main" tabIndex={-1} className="flex-1 overflow-y-auto outline-none">
          <PageTransition className="max-w-[1500px] mx-auto px-4 sm:px-7 lg:px-8 2xl:px-10 pt-2 pb-24 md:pb-8">{children}</PageTransition>
        </main>
      </div>
      <MobileBar onNav={() => { setCollapsed(false); setMobileOpen(true); }} />
      <ShortcutSheet open={shortcuts} onClose={() => setShortcuts(false)} />
      {aiStatus?.can_assist && <AssistantPanel open={assist} onClose={() => setAssist(false)} status={aiStatus} />}   {/* outside the header: its backdrop filter would become the containing block of the fixed drawer */}
    </div>
  );
}

function Header({ current, parentLabel, siblings, here, isFav, onFav, theme, toggleTheme, themeCustom, matchSystemTheme, logout, onMenu, menuOpen, aiStatus, onAssist }: { current?: string; parentLabel?: string; siblings: { href: string; label: string }[]; here?: string; isFav: boolean; onFav: () => void; theme: string; toggleTheme: () => void; themeCustom: boolean; matchSystemTheme: () => void; logout: () => void; onMenu: () => void; menuOpen: boolean; aiStatus: ReturnType<typeof useAssistantStatus>; onAssist: () => void }) {
  const { me, can } = useAuth();
  const persona = personaFor(me?.email);
  const router = useRouter();
  // The label for the page: a page that is not in the navigation (for example /admin/demo-control) is named from its address rather than shown as "Overview";
  // a module's own dashboard repeats its module name, so the parent is shown only when it adds something.
  const path = usePathname();
  const fromPath = path && path !== "/" ? path.split("?")[0].split("/").filter((x) => x && !/^\d+$/.test(x)).pop()?.replace(/[-_]/g, " ").replace(/^./, (c) => c.toUpperCase()) : undefined;
  const raw = parentLabel && current?.startsWith(`${parentLabel}: `) ? current.slice(parentLabel.length + 2) : current;
  const label = raw ?? fromPath ?? "Overview";
  const showParent = !!parentLabel && parentLabel !== label && !label.startsWith(parentLabel);
  const [menu, setMenu] = useState(false);
  const [create, setCreate] = useState(false);
  const [palette, setPalette] = useState(false);
  const [scan, setScan] = useState(false);
  const [sib, setSib] = useState(false);
  const sibRef = useRef<HTMLSpanElement>(null);
  useOutside(sibRef, () => setSib(false), sib);
  const gscan = useGlobalScan();
  useWedgeScanner(gscan.handle);
  const menuRef = useRef<HTMLDivElement>(null), createRef = useRef<HTMLDivElement>(null);
  useOutside(menuRef, () => setMenu(false), menu);
  useOutside(createRef, () => setCreate(false), create);
  useEffect(() => {
    const sc = () => setScan(true), pa = () => setPalette(true);
    window.addEventListener("mintree:scan", sc); window.addEventListener("mintree:palette", pa);
    return () => { window.removeEventListener("mintree:scan", sc); window.removeEventListener("mintree:palette", pa); };
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); } if (e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); setScan(true); } if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") { e.preventDefault(); onAssist(); } };
    document.addEventListener("keydown", k); return () => document.removeEventListener("keydown", k);
  }, [onAssist]);

  const quick = [
    { label: "Item", href: "/masters/items?new=1", perm: "masters.item:create" },
    { label: "Supplier", href: "/masters/suppliers?new=1", perm: "masters.supplier:create" },
    { label: "Customer", href: "/masters/customers?new=1", perm: "masters.customer:create" },
    { label: "Document", href: "/documents?new=1", perm: "documents.document:create" },
  ].filter((q) => can(q.perm));

  return (
    <header className="h-16 shrink-0"><div className="h-full max-w-[1440px] mx-auto flex items-center gap-2.5 sm:gap-3 px-4 sm:px-7">
      <button type="button" className="icon-btn md:hidden" onClick={onMenu} aria-label="Open navigation" aria-controls="primary-nav" aria-expanded={menuOpen}><Menu size={17} /></button>
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[0.75rem] text-muted min-w-0">
        <span className="hidden sm:inline">Mintree</span><ChevronRight size={13} className="hidden sm:block" />
        {showParent && <><span className="hidden sm:inline truncate">{parentLabel}</span><ChevronRight size={13} className="hidden sm:block shrink-0" /></>}
        <span key={label} className="text-fg font-medium truncate anim-fade">{label}</span>
        {siblings.length > 1 && (
          <span className="relative" ref={sibRef}>
            <button type="button" className="icon-btn !w-7 !h-7" aria-haspopup="menu" aria-expanded={sib} aria-label="Switch to a sibling screen" onClick={() => setSib((v) => !v)}><ChevronDown size={14} /></button>
            {sib && <div role="menu" className="anim-fade absolute left-0 mt-1.5 w-56 panel shadow-xl p-1.5 z-40 max-h-80 overflow-y-auto">
              {siblings.map((c) => <Link key={c.href + c.label} role="menuitem" href={c.href} onClick={() => setSib(false)} className={`block px-3 h-9 leading-9 rounded-xl text-[0.8125rem] hover:bg-inset truncate ${c.href === here ? "font-medium" : ""}`}>{c.label}</Link>)}
            </div>}
          </span>
        )}
        {here && here !== "/" && <button type="button" className="icon-btn !w-7 !h-7 shrink-0" onClick={onFav} aria-pressed={isFav} aria-label={isFav ? "Remove from pinned pages" : "Pin this page"} title={isFav ? "Unpin" : "Pin this page"}><Star size={14} strokeWidth={1.5} fill={isFav ? "currentColor" : "none"} style={isFav ? { color: "var(--acc-yellow)" } : undefined} /></button>}
      </nav>
      <div className="flex-1" />
      <button onClick={() => setScan(true)} className="btn" aria-label="Scan a barcode" title="Scan a barcode (Alt+S)"><ScanLine size={15} /><span className="hidden min-[1600px]:inline">Scan</span></button>
      {aiStatus?.can_assist && <button onClick={onAssist} className="btn" aria-label="Open the assistant" title="Assistant (Ctrl+J)" data-testid="ai-open"><MessageSquareText size={15} /><span className="hidden min-[1600px]:inline">Assistant</span></button>}
      <LiveIndicator />
      <DemoPill />
      <NotificationBell />
      <button onClick={() => setPalette(true)} className="hidden md:flex items-center gap-2 h-10 w-56 min-[1600px]:w-72 shrink-0 whitespace-nowrap px-4 rounded-full border border-line bg-surface text-muted text-[0.8125rem] hover:border-line-strong transition-colors">
        <Search size={14} />Search or jump to…<kbd className="ml-auto text-[0.75rem] whitespace-nowrap border border-line rounded-md px-1.5 py-0.5 bg-inset">Ctrl K</kbd>
      </button>
      {quick.length > 0 && (
        <div className="relative" ref={createRef}>
          <button className="btn btn-primary" onClick={() => setCreate((v) => !v)} aria-haspopup="menu" aria-expanded={create}><Plus size={14} aria-hidden /><span className="max-sm:sr-only">New</span><ChevronDown size={13} className="max-sm:hidden" aria-hidden /></button>
          {create && (
            <div role="menu" className="anim-fade absolute right-0 mt-2 w-48 panel shadow-xl p-1.5 z-40">
              {quick.map((q) => <button key={q.href} role="menuitem" className="w-full text-left px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={() => { setCreate(false); router.push(q.href); }}>{q.label}</button>)}
            </div>
          )}
        </div>
      )}
      <div className="relative" ref={menuRef}>
        <button onClick={() => setMenu((v) => !v)} className="flex items-center gap-2 h-10 pl-1.5 pr-3 rounded-full border border-line bg-surface hover:border-line-strong transition-colors" aria-haspopup="menu" aria-expanded={menu}>
          {persona
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={persona.photo} alt="" className="w-7 h-7 rounded-full object-cover" />
            : <span className="w-7 h-7 rounded-full grid place-items-center text-[0.75rem] font-semibold bg-primary text-[var(--primary-fg)]">{me?.full_name[0]}</span>}
          <ChevronDown size={13} className="text-muted" />
        </button>
        {menu && (
          <div role="menu" className="anim-fade absolute right-0 mt-1.5 w-60 panel shadow-xl z-40">
            <div className="px-3 py-2.5 border-b border-line">
              <div className="font-medium text-[0.8125rem] truncate">{persona?.name ?? me?.full_name}</div>
              {persona && <div className="text-muted text-[0.75rem] truncate">{persona.title}</div>}
              <div className="text-muted text-[0.75rem] truncate">{me?.email}</div>
              <div className="text-muted text-[0.75rem] mt-0.5">{me?.department_name ?? "No department"} · {me?.roles.join(", ") || "No role"}</div>
            </div>
            <div className="p-1">
              <button role="menuitem" className="w-full flex items-center gap-2 px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={toggleTheme}>
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}{theme === "dark" ? "Light appearance" : "Dark appearance"}
              </button>
              {themeCustom && <button role="menuitem" className="w-full flex items-center gap-2 px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={() => { setMenu(false); matchSystemTheme(); }}><Monitor size={14} />Match system appearance</button>}
              <button role="menuitem" className="w-full flex items-center gap-2 px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={() => { setMenu(false); router.push("/change-password"); }}><KeyRound size={14} />Change password</button>
              <button role="menuitem" className="w-full flex items-center gap-2 px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={logout}><LogOut size={14} />Sign out</button>
            </div>
          </div>
        )}
      </div>
      {palette && <Palette onClose={() => setPalette(false)} />}
      <ScanDialog open={scan} onClose={() => setScan(false)} onCode={(c) => { setScan(false); gscan.handle(c); }} />
      {gscan.dialog}
    </div></header>
  );
}

type Section = "Recent" | "Actions" | "Pages" | "Records";
type Hit = { label: string; sub?: string; href?: string; run?: "scan" | "shortcuts"; section: Section; icon?: LucideIcon; hint?: string };

/** Fuzzy match: every letter of the query must appear in order. Consecutive letters and a match at the start of a word score higher. -1 means no match. */
function fuzzy(q: string, text: string): number {
  if (!q) return 0;
  const t = text.toLowerCase(); let ti = 0, score = 0, run = 0;
  for (const ch of q) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return -1;
    run = at === ti ? run + 1 : 0;
    score += 1 + run * 2 + (at === 0 || t[at - 1] === " " ? 3 : 0);
    ti = at + 1;
  }
  return score - (t.length - q.length) * 0.05;
}

function Palette({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const router = useRouter();
  const [recent] = usePref<string[]>("recent", []);
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [records, setRecords] = useState<Hit[]>([]);
  const [sel, setSel] = useState(0);
  const query = q.trim().toLowerCase();

  const hits: Hit[] = useMemo(() => {
    const rank = (list: Hit[], text: (h: Hit) => string) => list.map((h) => [h, fuzzy(query, text(h))] as const).filter(([, sc]) => sc >= 0).sort((x, y) => y[1] - x[1]).map(([h]) => h);
    const actions: Hit[] = ACTIONS.filter((a) => !a.page && (!a.perm || can(a.perm))).map((a) => ({ label: a.label, sub: "Action", href: a.href, run: a.run === "palette" ? undefined : a.run, section: "Actions", icon: a.icon, hint: a.shortcut }));
    actions.push({ label: "Keyboard shortcuts and what's new", sub: "Action", run: "shortcuts", section: "Actions", hint: "?" });
    const pages: Hit[] = ALL_NAV_ITEMS.filter((i) => visible(i, can)).map((i) => ({ label: i.label, sub: i.group, href: i.href, section: "Pages" as const, icon: i.icon }));
    if (!query) {
      const rec: Hit[] = recent.map((h) => ALL_NAV_ITEMS.find((i) => i.href === h && visible(i, can))).filter((i): i is NonNullable<typeof i> => !!i).slice(0, 5).map((i) => ({ label: i.label, sub: i.group, href: i.href, section: "Recent" as const, icon: i.icon }));
      return [...rec, ...actions.slice(0, 6)];
    }
    const withKeys = (h: Hit) => `${h.label} ${ACTIONS.find((a) => a.label === h.label)?.keywords ?? ""} ${ALL_NAV_ITEMS.find((i) => i.label === h.label)?.keywords ?? ""}`;
    return [...rank(actions, withKeys).slice(0, 4), ...rank(pages, withKeys).slice(0, 6), ...records];
  }, [query, can, recent, records]);

  useEffect(() => {
    const t = dq.trim();
    if (t.length < 2) { setRecords([]); return; }
    let live = true;
    const jobs: Promise<Hit[]>[] = [];
    type Rec = Record<string, string>;
    const add = (perm: string, path: string, map: (r: Rec) => Hit) => {
      if (can(`${perm}:read`)) jobs.push(api<Page<Rec & { id: number }>>(path, { params: { q: t, page_size: 3 } }).then((p) => p.items.map(map)).catch(() => []));
    };
    add("masters.item", "/masters/items", (r) => ({ label: r.name, sub: `Item · ${r.item_code}`, href: `/masters/items?q=${encodeURIComponent(r.item_code)}`, section: "Records" }));
    add("masters.supplier", "/masters/suppliers", (r) => ({ label: r.name, sub: `Supplier · ${r.code}`, href: `/masters/suppliers?q=${encodeURIComponent(r.code)}`, section: "Records" }));
    add("masters.customer", "/masters/customers", (r) => ({ label: r.name, sub: `Customer · ${r.code}`, href: `/crm/customers/${(r as unknown as { id: number }).id}`, section: "Records" }));
    /* batch labels, order and invoice numbers: the same resolver the barcode scanner uses */
    if (t.length >= 3) jobs.push(resolveCode(t).then((r) => r.matches.slice(0, 4).map((m) => ({ label: m.label, sub: `${m.type_label}${m.status ? ` · ${m.status}` : ""}`, href: m.link, section: "Records" as const }))).catch(() => []));
    Promise.all(jobs).then((r) => { if (live) setRecords(r.flat()); });
    return () => { live = false; };
  }, [dq, can]);

  useEffect(() => setSel(0), [q]);
  const go = (h: Hit) => { onClose(); if (h.run) runShell(h.run); else if (h.href) router.push(h.href); };
  const sections = (["Recent", "Actions", "Pages", "Records"] as Section[]).map((name) => ({ name, items: hits.filter((h) => h.section === name) })).filter((g) => g.items.length);
  const flat = sections.flatMap((g) => g.items);

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[14vh] px-4">
      <div className="absolute inset-0 anim-fade" onClick={onClose} style={{ background: "rgb(21 23 23 / 0.28)", backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)" }} />
      <div role="dialog" aria-modal="true" aria-label="Search" className="anim-fade relative panel w-full max-w-xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 border-b border-line">
          <Search size={15} className="text-muted" />
          <input autoFocus className="flex-1 h-12 bg-transparent outline-none focus-visible:!outline-none text-[0.875rem]" placeholder="Search pages, actions and records…" value={q} role="combobox" aria-expanded aria-controls="palette-list" aria-activedescendant={flat[sel] ? `pal-${sel}` : undefined}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel((x) => Math.min(x + 1, flat.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((x) => Math.max(x - 1, 0)); }
              else if (e.key === "Enter" && flat[sel]) go(flat[sel]);
            }} />
          <kbd className="text-[0.6875rem] border border-line rounded-md px-1.5 py-0.5 bg-inset text-muted">Esc</kbd>
        </div>
        <div id="palette-list" role="listbox" className="max-h-[22rem] overflow-y-auto p-1.5">
          {flat.length === 0 && <p className="px-3 py-8 text-center text-muted text-[0.8125rem]">Nothing matches &ldquo;{q}&rdquo;</p>}
          {sections.map((g) => (
            <div key={g.name} role="group" aria-label={g.name}>
              <p className="px-2.5 pt-2 pb-1 text-[0.6875rem] text-muted">{g.name}</p>
              {g.items.map((h) => {
                const i = flat.indexOf(h); const Ic = h.icon;
                return (
                  <button key={h.section + h.label + (h.href ?? h.run)} id={`pal-${i}`} role="option" aria-selected={i === sel} onMouseEnter={() => { setSel(i); if (h.href) router.prefetch(h.href); }} onFocus={() => { if (h.href) router.prefetch(h.href); }} onClick={() => go(h)}
                    className={`w-full text-left flex items-center gap-2.5 px-2.5 h-10 rounded-xl text-[0.8125rem] ${i === sel ? "bg-surface-2" : ""}`}>
                    {Ic ? <Ic size={15} strokeWidth={1.5} className="shrink-0 text-muted" aria-hidden /> : <span className="w-[15px]" aria-hidden />}
                    <span className="truncate flex-1">{h.label}</span>
                    {h.sub && <span className="text-muted text-[0.75rem] shrink-0 truncate max-w-[10rem]">{h.sub}</span>}
                    {h.hint && <kbd className="text-[0.6875rem] border border-line rounded-md px-1.5 py-0.5 bg-inset text-muted shrink-0">{h.hint}</kbd>}
                    {i === sel && <CornerDownLeft size={13} className="text-muted shrink-0" aria-hidden />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="flex items-center gap-4 px-4 h-9 border-t border-line text-[0.6875rem] text-muted"><span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span className="ml-auto">? for all shortcuts</span></div>
      </div>
    </div>
  );
}

function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const toast = useToast();
  useLiveEvent(["notification.new"], (e) => {                                  // a new alert for this person: the count updates at once; urgent ones also show a toast
    window.dispatchEvent(new Event("notifications-changed"));
    if (e.data.priority === "HIGH" || e.data.priority === "CRITICAL") toast("error", String(e.data.title ?? "New alert"));
  });
  useEffect(() => {
    let live = true;
    const load = () => api<{ unread: number }>("/notifications", { params: { page_size: 1 } }).then((d) => live && setUnread(d.unread)).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    window.addEventListener("notifications-changed", load);
    return () => { live = false; clearInterval(t); window.removeEventListener("notifications-changed", load); };
  }, []);
  return (
    <Link href="/notifications" className="relative icon-btn" aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}>
      <BellIcon size={17} strokeWidth={1.75} />
      {unread > 0 && <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--gold)] text-[var(--gold-ink)] ring-2 ring-[var(--canvas)] text-[0.75rem] leading-[1.125rem] text-center font-medium" data-testid="unread-count">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );
}





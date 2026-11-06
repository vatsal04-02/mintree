"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ScanLine, Bell as BellIcon, ChevronDown, ChevronRight, KeyRound, LogOut, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Sun } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ALL_NAV_ITEMS, NAV, visible } from "@/lib/nav";
import { api, type Page } from "@/lib/api";
import { useDebounced } from "./ui";
import { ScanDialog, useGlobalScan, useWedgeScanner } from "./Scanner";

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    try { const t = localStorage.getItem("mintree-theme"); if (t === "dark" || t === "light") setTheme(t); else if (matchMedia("(prefers-color-scheme: dark)").matches) setTheme("dark"); } catch {}
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("mintree-theme", theme); } catch {}
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))] as const;
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
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggleGroup = (href: string, open: boolean) => setExpanded((e) => ({ ...e, [href]: !open }));
  const [theme, toggleTheme] = useTheme();
  useEffect(() => { try { setCollapsed(localStorage.getItem("mintree-nav") === "1"); } catch {} }, []);
  const toggleNav = () => setCollapsed((c) => { try { localStorage.setItem("mintree-nav", c ? "0" : "1"); } catch {} return !c; });

  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => visible(i, can)) })).filter((g) => g.items.length);
  const current = [...ALL_NAV_ITEMS].filter((i) => (i.href === "/" ? pathname === "/" : pathname.startsWith(i.href))).sort((a, b) => b.href.length - a.href.length)[0];

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className={`${collapsed ? "w-[60px]" : "w-[236px]"} shrink-0 flex flex-col transition-[width] duration-150`} style={{ background: "var(--sidebar)", color: "var(--sidebar-fg)" }}>
        <div className="h-14 flex items-center gap-2.5 px-3.5 shrink-0">
          <div className="w-8 h-8 rounded-lg grid place-items-center font-semibold text-[15px] shrink-0" style={{ background: "var(--sidebar-active)", color: "#fff" }} aria-hidden>M</div>
          {!collapsed && <div className="leading-tight"><div className="text-white font-semibold text-[14px]">Mintree</div><div className="text-[11px] opacity-70">Factory OS</div></div>}
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-3 sidebar-scroll" aria-label="Main">
          {groups.map((g) => (
            <div key={g.label} className="mt-3 first:mt-1">
              {!collapsed && <div className="px-2.5 mb-1 text-[10.5px] uppercase tracking-[0.08em] opacity-55 font-medium">{g.label}</div>}
              {g.items.map((i) => {
                const active = i === current || (i.href === "/" ? pathname === "/" : pathname.startsWith(i.href));
                const Icon = i.icon;
                if (i.children) {
                  const kids = i.children.filter((c) => !c.perm || can(c.perm));
                  const open = !collapsed && (expanded[i.href] ?? pathname.startsWith(i.href));
                  return (
                    <div key={i.href} className="mb-px">
                      <button type="button" aria-expanded={open} title={collapsed ? i.label : undefined}
                        onClick={() => (collapsed ? router.push(kids[0]?.href ?? i.href) : toggleGroup(i.href, open))}
                        className={`flex items-center gap-2.5 h-8 px-2.5 rounded-md text-[13px] w-full text-left transition-colors ${active ? "text-white font-medium" : "hover:bg-white/5"}`}>
                        <Icon size={16} className="shrink-0" />
                        {!collapsed && <><span className="truncate flex-1">{i.label}</span><ChevronRight size={13} className={`shrink-0 opacity-60 transition-transform ${open ? "rotate-90" : ""}`} /></>}
                      </button>
                      {open && (
                        <div className="ml-[19px] pl-3 border-l border-white/10 my-0.5">
                          {kids.map((c) => {
                            const here = c.href === `/m/${i.href.split("/")[2]}` ? pathname === c.href : pathname === c.href || pathname.startsWith(`${c.href}/`);
                            return (
                              <Link key={c.href + c.label} href={c.href} aria-current={here ? "page" : undefined}
                                className={`block h-7 leading-7 px-2.5 rounded-md text-[12.5px] truncate transition-colors ${here ? "text-white bg-white/10 font-medium" : "opacity-75 hover:opacity-100 hover:bg-white/5"}`}>{c.label}</Link>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                }
                return (
                  <Link key={i.href} href={i.href} title={collapsed ? i.label : undefined} aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-2.5 h-8 px-2.5 rounded-md text-[13px] mb-px transition-colors ${active ? "text-white font-medium" : "hover:bg-white/5"}`}
                    style={active ? { background: "var(--sidebar-active)" } : undefined}>
                    <Icon size={16} className="shrink-0" />{!collapsed && <span className="truncate">{i.label}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="border-t border-white/10 p-2">
          <button onClick={toggleNav} className="flex items-center gap-2.5 h-8 px-2.5 rounded-md w-full text-[12.5px] hover:bg-white/5" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <PanelLeftOpen size={16} /> : <><PanelLeftClose size={16} />Collapse</>}
          </button>
          <div className="flex items-center gap-2.5 px-2 py-2 mt-1">
            <div className="w-7 h-7 rounded-full grid place-items-center text-[11px] font-semibold shrink-0" style={{ background: "var(--sidebar-active)", color: "#fff" }}>
              {me?.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase()}
            </div>
            {!collapsed && <div className="min-w-0 leading-tight"><div className="text-white text-[12.5px] truncate">{me?.full_name}</div><div className="text-[11px] opacity-65 truncate">{me?.roles[0] ?? "No role"}</div></div>}
          </div>
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <Header current={current?.label} theme={theme} toggleTheme={toggleTheme} logout={logout} />
        <main className="flex-1 overflow-y-auto">
          <div className="max-w-[1320px] mx-auto px-7 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}

function Header({ current, theme, toggleTheme, logout }: { current?: string; theme: string; toggleTheme: () => void; logout: () => void }) {
  const { me, can } = useAuth();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [create, setCreate] = useState(false);
  const [palette, setPalette] = useState(false);
  const [scan, setScan] = useState(false);
  const gscan = useGlobalScan();
  useWedgeScanner(gscan.handle);
  const menuRef = useRef<HTMLDivElement>(null), createRef = useRef<HTMLDivElement>(null);
  useOutside(menuRef, () => setMenu(false), menu);
  useOutside(createRef, () => setCreate(false), create);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); } if (e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); setScan(true); } };
    document.addEventListener("keydown", k); return () => document.removeEventListener("keydown", k);
  }, []);

  const quick = [
    { label: "Item", href: "/masters/items?new=1", perm: "masters.item:create" },
    { label: "Supplier", href: "/masters/suppliers?new=1", perm: "masters.supplier:create" },
    { label: "Customer", href: "/masters/customers?new=1", perm: "masters.customer:create" },
    { label: "Document", href: "/documents?new=1", perm: "documents.document:create" },
  ].filter((q) => can(q.perm));

  return (
    <header className="h-14 shrink-0 border-b border-line bg-surface flex items-center gap-4 px-7">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[12.5px] text-muted min-w-0">
        <span>Mintree</span><ChevronRight size={13} /><span className="text-fg font-medium truncate">{current ?? "Overview"}</span>
      </nav>
      <div className="flex-1" />
      <button onClick={() => setScan(true)} className="btn btn-sm" aria-label="Scan a barcode" title="Scan a barcode (Alt+S)"><ScanLine size={15} /><span className="hidden lg:inline">Scan</span></button>
      <NotificationBell />
      <button onClick={() => setPalette(true)} className="hidden md:flex items-center gap-2 h-8 w-72 px-2.5 rounded-md border border-line bg-bg text-muted text-[12.5px] hover:border-muted/50">
        <Search size={14} />Search or jump to…<kbd className="ml-auto text-[10.5px] border border-line rounded px-1 bg-surface">Ctrl K</kbd>
      </button>
      {quick.length > 0 && (
        <div className="relative" ref={createRef}>
          <button className="btn btn-primary" onClick={() => setCreate((v) => !v)} aria-haspopup="menu" aria-expanded={create}><Plus size={14} />New<ChevronDown size={13} /></button>
          {create && (
            <div role="menu" className="anim-fade absolute right-0 mt-1.5 w-44 panel shadow-xl p-1 z-40">
              {quick.map((q) => <button key={q.href} role="menuitem" className="w-full text-left px-2.5 h-8 rounded text-[13px] hover:bg-surface-2" onClick={() => { setCreate(false); router.push(q.href); }}>{q.label}</button>)}
            </div>
          )}
        </div>
      )}
      <div className="relative" ref={menuRef}>
        <button onClick={() => setMenu((v) => !v)} className="flex items-center gap-2 h-8 pl-1 pr-2 rounded-md hover:bg-surface-2" aria-haspopup="menu" aria-expanded={menu}>
          <span className="w-6 h-6 rounded-full grid place-items-center text-[10.5px] font-semibold bg-primary text-[var(--primary-fg)]">{me?.full_name[0]}</span>
          <ChevronDown size={13} className="text-muted" />
        </button>
        {menu && (
          <div role="menu" className="anim-fade absolute right-0 mt-1.5 w-60 panel shadow-xl z-40">
            <div className="px-3 py-2.5 border-b border-line">
              <div className="font-medium text-[13px] truncate">{me?.full_name}</div>
              <div className="text-muted text-[12px] truncate">{me?.email}</div>
              <div className="text-muted text-[11.5px] mt-0.5">{me?.department_name ?? "No department"} · {me?.roles.join(", ") || "No role"}</div>
            </div>
            <div className="p-1">
              <button role="menuitem" className="w-full flex items-center gap-2 px-2.5 h-8 rounded text-[13px] hover:bg-surface-2" onClick={toggleTheme}>
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}{theme === "dark" ? "Light theme" : "Dark theme"}
              </button>
              <button role="menuitem" className="w-full flex items-center gap-2 px-2.5 h-8 rounded text-[13px] hover:bg-surface-2" onClick={() => { setMenu(false); router.push("/change-password"); }}><KeyRound size={14} />Change password</button>
              <button role="menuitem" className="w-full flex items-center gap-2 px-2.5 h-8 rounded text-[13px] hover:bg-surface-2" onClick={logout}><LogOut size={14} />Sign out</button>
            </div>
          </div>
        )}
      </div>
      {palette && <Palette onClose={() => setPalette(false)} />}
      <ScanDialog open={scan} onClose={() => setScan(false)} onCode={(c) => { setScan(false); gscan.handle(c); }} />
      {gscan.dialog}
    </header>
  );
}

type Hit = { label: string; sub?: string; href: string };
function Palette({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [records, setRecords] = useState<Hit[]>([]);
  const [sel, setSel] = useState(0);

  const pages: Hit[] = useMemo(() => {
    const t = q.trim().toLowerCase();
    return ALL_NAV_ITEMS.filter((i) => visible(i, can) && (!t || `${i.label} ${i.keywords ?? ""}`.toLowerCase().includes(t)))
      .map((i) => ({ label: i.label, sub: i.group, href: i.href }));
  }, [q, can]);

  useEffect(() => {
    const t = dq.trim();
    if (t.length < 2) { setRecords([]); return; }
    let live = true;
    const jobs: Promise<Hit[]>[] = [];
    type Rec = Record<string, string>;
    const add = (perm: string, path: string, map: (r: Rec) => Hit) => {
      if (can(`${perm}:read`)) jobs.push(api<Page<Rec & { id: number }>>(path, { params: { q: t, page_size: 4 } }).then((p) => p.items.map(map)).catch(() => []));
    };
    add("masters.item", "/masters/items", (r) => ({ label: r.name, sub: `Item · ${r.item_code}`, href: `/masters/items?q=${encodeURIComponent(r.item_code)}` }));
    add("masters.supplier", "/masters/suppliers", (r) => ({ label: r.name, sub: `Supplier · ${r.code}`, href: `/masters/suppliers?q=${encodeURIComponent(r.code)}` }));
    add("masters.customer", "/masters/customers", (r) => ({ label: r.name, sub: `Customer · ${r.code}`, href: `/masters/customers?q=${encodeURIComponent(r.code)}` }));
    Promise.all(jobs).then((r) => { if (live) setRecords(r.flat()); });
    return () => { live = false; };
  }, [dq, can]);

  const all = [...pages, ...records];
  useEffect(() => setSel(0), [q]);
  const go = (h: Hit) => { onClose(); router.push(h.href); };

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[14vh] px-4">
      <div className="absolute inset-0 bg-black/40 anim-fade" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Search" className="anim-fade relative panel w-full max-w-xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 px-3.5 border-b border-line">
          <Search size={15} className="text-muted" />
          <input autoFocus className="flex-1 h-11 bg-transparent outline-none text-[14px]" placeholder="Search pages, items, suppliers, customers…" value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, all.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
              else if (e.key === "Enter" && all[sel]) go(all[sel]);
            }} />
        </div>
        <ul className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {all.length === 0 && <li className="px-3 py-6 text-center text-muted text-[13px]">No results</li>}
          {all.map((h, i) => (
            <li key={h.href + h.label + i} role="option" aria-selected={i === sel}>
              <button className={`w-full text-left flex items-center justify-between gap-3 px-2.5 h-9 rounded-md text-[13px] ${i === sel ? "bg-surface-2" : ""}`} onMouseEnter={() => setSel(i)} onClick={() => go(h)}>
                <span className="truncate">{h.label}</span><span className="text-muted text-[11.5px] shrink-0">{h.sub}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}


function NotificationBell() {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    let live = true;
    const load = () => api<{ unread: number }>("/notifications", { params: { page_size: 1 } }).then((d) => live && setUnread(d.unread)).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    window.addEventListener("notifications-changed", load);
    return () => { live = false; clearInterval(t); window.removeEventListener("notifications-changed", load); };
  }, []);
  return (
    <Link href="/notifications" className="relative btn btn-ghost btn-sm !px-2" aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}>
      <BellIcon size={16} />
      {unread > 0 && <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-danger text-white text-[10px] leading-4 text-center font-medium" data-testid="unread-count">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );
}

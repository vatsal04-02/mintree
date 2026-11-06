"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, setCsrf, setUnauthorizedHandler } from "./api";

export type Me = {
  id: number; email: string; full_name: string; department_name: string | null;
  roles: string[]; permissions: string[]; must_change_password: boolean; csrf_token: string;
};

type Ctx = {
  me: Me | null; loading: boolean;
  can: (perm: string) => boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};
const AuthCtx = createContext<Ctx>(null as unknown as Ctx);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const apply = (m: Me | null) => { setCsrf(m?.csrf_token ?? null); setMe(m); };

  const refresh = useCallback(async () => {
    try { apply(await api<Me>("/auth/me")); } catch { apply(null); } finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    setUnauthorizedHandler(() => { apply(null); router.replace("/login"); });
    return () => setUnauthorizedHandler(null);
  }, [router]);

  // Route guard: unauthenticated -> /login; forced password change -> /change-password.
  useEffect(() => {
    if (loading) return;
    if (!me && pathname !== "/login") router.replace("/login");
    else if (me?.must_change_password && pathname !== "/change-password") router.replace("/change-password");
  }, [me, loading, pathname, router]);

  const value = useMemo<Ctx>(() => {
    const perms = new Set(me?.permissions ?? []);
    return {
      me, loading, refresh,
      can: (p) => perms.has(p),
      login: async (email, password) => { const m = await api<Me>("/auth/login", { body: { email, password } }); apply(m); return m; },
      logout: async () => { try { await api("/auth/logout", { method: "POST" }); } finally { apply(null); router.replace("/login"); } },
    };
  }, [me, loading, refresh, router]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

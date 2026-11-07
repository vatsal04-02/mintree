export class ApiError extends Error {
  constructor(public status: number, message: string, public fields?: Record<string, string>) {
    super(message);
  }
}

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

type Opts = { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string>; params?: Record<string, string | number | boolean | null | undefined> };

function qs(params?: Opts["params"]) {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** FastAPI returns `detail` as a string, or as a list of {loc,msg} for validation errors. */
function parseDetail(detail: unknown): { message: string; fields?: Record<string, string> } {
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    for (const d of detail) {
      const loc = Array.isArray(d.loc) ? d.loc.filter((x: string) => x !== "body").join(".") : "";
      fields[loc] = String(d.msg).replace(/^Value error, /, "");
    }
    return { message: Object.values(fields)[0] ?? "Validation failed", fields };
  }
  // Business-rule refusals arrive as {code, message, ...context}: show the sentence, not a generic failure.
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") return { message: (detail as { message: string }).message };
  return { message: "Request failed" };
}

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.body || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  const res = await fetch(`/api${path}${qs(opts.params)}`, { method, headers, body, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/login")) onUnauthorized?.();
    const { message, fields } = parseDetail((data as { detail?: unknown })?.detail);
    throw new ApiError(res.status, message, fields);
  }
  return data as T;
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

/** GET with in-flight de-duplication and a short client-side freshness window, for read-mostly aggregates only (dashboards, status flags).
 *  Never use it for stock availability, balances or anything a user acts on within seconds: those must always be fetched fresh. */
const shared = new Map<string, { at: number; p: Promise<unknown> }>();
export function sharedGet<T>(path: string, params?: Opts["params"], ttlMs = 10_000): Promise<T> {
  const key = path + qs(params);
  const hit = shared.get(key);
  if (hit && Date.now() - hit.at < ttlMs) return hit.p as Promise<T>;
  const p = api<T>(path, { params });
  shared.set(key, { at: Date.now(), p });
  p.catch(() => shared.delete(key));                      // a failure is never remembered
  return p;
}
/** Forget shared reads (call after a business write so the next screen shows the change). */
export const clearShared = () => shared.clear();

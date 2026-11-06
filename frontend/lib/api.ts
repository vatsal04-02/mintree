export class ApiError extends Error {
  constructor(public status: number, message: string, public fields?: Record<string, string>) {
    super(message);
  }
}

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

type Opts = { method?: string; body?: unknown; form?: FormData; params?: Record<string, string | number | boolean | null | undefined> };

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
  return { message: "Request failed" };
}

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.body || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
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

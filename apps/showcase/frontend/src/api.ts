const TOKEN_KEY = "cw.token";
const NAME_KEY = "cw.name";

export const session = {
  token: () => sessionStorage.getItem(TOKEN_KEY),
  name: () => sessionStorage.getItem(NAME_KEY) ?? "",
  set(token: string, name: string) {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(NAME_KEY, name);
  },
  clear() {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(NAME_KEY);
  },
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Turns either error shape the API returns into one message. */
function errorFrom(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { loc?: string[]; msg?: string };
    const field = first?.loc?.[first.loc.length - 1] ?? "input";
    return new ApiError(status, "VALIDATION", `${field}: ${first?.msg ?? "invalid"}`);
  }
  if (detail && typeof detail === "object") {
    const d = detail as { code?: string; message?: string };
    return new ApiError(status, d.code ?? "ERROR", d.message ?? "Request failed");
  }
  return new ApiError(status, "ERROR", `Request failed (${status})`);
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = session.token();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/login") {
      session.clear();
      window.location.assign("/login");
    }
    throw errorFrom(res.status, data);
  }
  return data as T;
}

export interface CaseRow {
  case_no: string;
  customer: string;
  title: string;
  status: string;
  priority: string;
}

export interface CaseDetail {
  case_no: string;
  title: string;
  status: string;
  priority: string;
  category_id: number | null;
  category: string | null;
  subcategory_id: number | null;
  subcategory: string | null;
  escalation_reason: string | null;
  opened_at: string;
  updated_at: string;
  customer: { cust_no: string; full_name: string; email: string; phone: string; city: string };
}

export interface Option {
  id: number;
  code: string;
  name: string;
}

export interface Note {
  id: number;
  text: string;
  author: string;
  created_at: string;
}

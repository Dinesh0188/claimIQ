import type {
  AskResponse,
  AuditResult,
  ClaimDetail,
  ClaimPacket,
  ClaimsListResponse,
  ExtractionResult,
  HealthResponse,
  LeakageItem,
  MissingDoc,
  PortfolioSummary,
  ProfileConfig,
  RecoveryModel,
  RuleCatalog,
  RuleChunk,
  TopLeakingItem,
  TraceResponse,
} from "./types";
import { getApiKey } from "./auth";

// In production (Vercel), the browser calls the backend directly at an absolute URL --
// set NEXT_PUBLIC_API_URL to the deployed backend's origin (e.g. https://claimiq-api.onrender.com).
// Routing file uploads through a Vercel rewrite would hit the platform's serverless
// body-size limit, so direct browser-to-backend calls are the correct shape once the
// two are deployed separately. Left empty for local dev, where next.config.js
// rewrites /api, /health and /v1 to http://localhost:8000 and relative paths suffice.
const BASE = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { "X-API-Key": key } : {};
}

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function unwrap<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    // not JSON
  }

  if (!res.ok) {
    let detail: string;
    if (body && typeof body === "object" && "detail" in body) {
      const d = (body as { detail: unknown }).detail;
      if (Array.isArray(d)) {
        detail = d
          .map((item: { loc?: string[]; msg?: string }) =>
            `${(item.loc || []).slice(1).join(".")}: ${item.msg}`
          )
          .join("; ");
      } else if (typeof d === "object") {
        detail = JSON.stringify(d);
      } else {
        detail = String(d);
      }
    } else {
      detail = text || res.statusText;
    }
    throw new ApiError(detail.slice(0, 500), res.status);
  }

  return body as T;
}

export const api = {
  async get<T>(path: string, params: Record<string, string | number | boolean> = {}): Promise<T> {
    const qs = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== "" && v != null)
        .map(([k, v]) => [k, String(v)])
    ).toString();
    const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
      headers: authHeaders(),
    });
    return unwrap<T>(res);
  },

  async post<T>(path: string, payload?: unknown, params: Record<string, string> = {}): Promise<T> {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(payload ?? {}),
    });
    return unwrap<T>(res);
  },

  async upload<T>(path: string, file: File): Promise<T> {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    });
    return unwrap<T>(res);
  },

  async pdf(path: string, payload: unknown, params: Record<string, string> = {}): Promise<Blob> {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      await unwrap(res); // throws
    }
    return res.blob();
  },
};

// -- Typed API functions --

export const claimiqApi = {
  health: () => api.get<HealthResponse>("/health"),

  profiles: () => api.get<Record<string, ProfileConfig>>("/api/profiles"),

  samples: () => api.get<string[]>("/api/samples"),

  getSample: (name: string) => api.get<ClaimPacket>(`/api/samples/${name}`),

  extract: (file: File) => api.upload<ExtractionResult>("/api/extract", file),

  audit: (packet: ClaimPacket) => api.post<AuditResult>("/api/audit", packet),

  report: (packet: ClaimPacket, profile: string) =>
    api.pdf("/api/report", packet, { profile }),

  claims: (params: { q?: string; month?: string; limit?: number; offset?: number } = {}) =>
    api.get<ClaimsListResponse>("/api/claims", params as Record<string, string | number>),

  claimDetail: (claimId: string) => api.get<ClaimDetail>(`/api/claims/${claimId}`),

  trace: (claimId: string) => api.get<TraceResponse>(`/api/trace/${encodeURIComponent(claimId)}`),

  rule: (chunkId: string) => api.get<RuleChunk>(`/api/rules/${encodeURIComponent(chunkId)}`),

  rulesCatalog: (full = true) => api.get<RuleCatalog>("/api/rules", { full }),

  analyticsSummary: () => api.get<PortfolioSummary>("/api/analytics/summary"),

  analyticsLeakage: () => api.get<LeakageItem[]>("/api/analytics/leakage"),

  analyticsTopItems: (limit = 10) =>
    api.get<TopLeakingItem[]>("/api/analytics/top-items", { limit }),

  analyticsMissingDocs: (limit = 10) =>
    api.get<MissingDoc[]>("/api/analytics/missing-docs", { limit }),

  recovery: (annualClaimVolume = 0, limit = 12) =>
    api.get<RecoveryModel>("/api/analytics/recovery", {
      annual_claim_volume: String(annualClaimVolume),
      limit: String(limit),
    }),

  ask: (question: string) => api.post<AskResponse>("/api/analytics/ask", { question }),
};

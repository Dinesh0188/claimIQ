// Thin API client. One place that knows how the server reports failure.

const BASE = "";

/** Unwrap a response, or throw an Error carrying what the server actually said.
 *
 * Calling .json() unconditionally is how "Unexpected token < in JSON" reaches a user:
 * the body was HTML or empty, and the parse failure replaced the real cause. */
async function unwrap(res) {
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* not JSON */ }

  if (!res.ok) {
    let detail = body?.detail ?? text ?? res.statusText;
    // FastAPI validation errors are a list of objects, which stringify to
    // "[object Object]" -- useless on screen. Flatten to something readable.
    if (Array.isArray(detail)) {
      detail = detail
        .map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`)
        .join("; ");
    } else if (detail && typeof detail === "object") {
      detail = JSON.stringify(detail);
    }
    const err = new Error(String(detail).slice(0, 500));
    err.status = res.status;
    throw err;
  }
  return body;
}

export const api = {
  async get(path, params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== "" && v != null)
    ).toString();
    return unwrap(await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`));
  },

  async post(path, payload, params = {}) {
    const qs = new URLSearchParams(params).toString();
    return unwrap(
      await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      })
    );
  },

  async upload(path, file) {
    const form = new FormData();
    form.append("file", file, file.name);
    return unwrap(await fetch(`${BASE}${path}`, { method: "POST", body: form }));
  },

  /** The report endpoint returns a PDF, not JSON. */
  async pdf(path, payload, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return unwrap(res); // throws with the real detail
    return res.blob();
  },
};

// Rules are immutable for the life of a corpus version and a findings list asks for
// the same handful repeatedly, so they are cached rather than refetched per expander.
const ruleCache = new Map();
export async function rule(chunkId) {
  if (!ruleCache.has(chunkId)) {
    ruleCache.set(chunkId, api.get(`/api/rules/${encodeURIComponent(chunkId)}`));
  }
  try {
    return await ruleCache.get(chunkId);
  } catch (err) {
    ruleCache.delete(chunkId); // a failed lookup must not be cached forever
    throw err;
  }
}

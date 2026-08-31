const KEY = "claimiq.api_key";

// Prefer sessionStorage for demo (cleared on tab close) rather than long-lived localStorage.
// Fall back to localStorage for backwards compat and migrate existing keys to sessionStorage.
function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    if (window.sessionStorage) return window.sessionStorage;
  } catch {}
  return null;
}

function legacyStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function getApiKey(): string | null {
  const s = storage();
  if (s) {
    const v = s.getItem(KEY);
    if (v) return v;
  }
  // migrate from legacy localStorage if present
  const legacy = legacyStorage()?.getItem(KEY);
  if (legacy) {
    s?.setItem(KEY, legacy);
    legacyStorage()?.removeItem(KEY);
    return legacy;
  }
  return null;
}
export function setApiKey(k: string): void {
  const trimmed = k.trim();
  // Never prefill a deployed key into bundle; this is user-provided only
  storage()?.setItem(KEY, trimmed);
}
export function clearApiKey(): void {
  storage()?.removeItem(KEY);
  legacyStorage()?.removeItem(KEY);
}
export function hasApiKey(): boolean {
  return Boolean(getApiKey());
}

const KEY = "claimiq.api_key";

export function getApiKey(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(KEY);
}
export function setApiKey(k: string): void {
  window.localStorage.setItem(KEY, k.trim());
}
export function clearApiKey(): void {
  window.localStorage.removeItem(KEY);
}
export function hasApiKey(): boolean {
  return Boolean(getApiKey());
}

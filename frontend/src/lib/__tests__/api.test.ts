import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "../api";

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

describe("api error parsing", () => {
  beforeEach(() => vi.resetAllMocks());

  it("parses detail string error", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      text: async () => JSON.stringify({ detail: "invalid claim" }),
    } as Response);

    await expect(api.get("/api/test")).rejects.toThrow("invalid claim");
  });

  it("parses detail array with loc/msg", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 422,
      statusText: "Unprocessable",
      text: async () =>
        JSON.stringify({
          detail: [
            { loc: ["body", "claim_id"], msg: "field required" },
            { loc: ["body", "gross_bill"], msg: "ensure > 0" },
          ],
        }),
    } as Response);

    await expect(api.get("/api/test")).rejects.toThrow(
      "claim_id: field required; gross_bill: ensure > 0"
    );
  });

  it("supports AbortSignal passthrough", async () => {
    const controller = new AbortController();
    mockFetch.mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ ok: true }),
    } as Response);

    const res = await api.get<{ ok: boolean }>("/health", {}, { signal: controller.signal });
    expect(res).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal })
    );
  });
});

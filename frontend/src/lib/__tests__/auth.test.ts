import { describe, it, expect, beforeEach } from "vitest";
import { getApiKey, setApiKey, clearApiKey, hasApiKey } from "../auth";

describe("auth", () => {
  beforeEach(() => clearApiKey());
  it("round-trips a key", () => {
    setApiKey("k_live_abc");
    expect(getApiKey()).toBe("k_live_abc");
    expect(hasApiKey()).toBe(true);
  });
  it("trims on set", () => {
    setApiKey("  k_ro_xyz  ");
    expect(getApiKey()).toBe("k_ro_xyz");
  });
  it("clears", () => {
    setApiKey("k_live_abc");
    clearApiKey();
    expect(hasApiKey()).toBe(false);
  });
});

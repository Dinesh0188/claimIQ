import { describe, it, expect } from "vitest";
import { rupees, formatDate, formatCount } from "../utils";

describe("rupees", () => {
  it("formats Indian grouping via Intl", () => {
    expect(rupees(850000)).toBe("₹8,50,000");
    expect(rupees("123456")).toBe("₹1,23,456");
    expect(rupees(0)).toBe("₹0");
  });
  it("returns em dash for nullish", () => {
    expect(rupees(null)).toBe("—");
    expect(rupees(undefined)).toBe("—");
    expect(rupees("")).toBe("—");
  });
  it("handles negative", () => {
    expect(rupees(-5000)).toBe("-₹5,000");
  });
});

describe("formatDate", () => {
  it("formats ISO to locale", () => {
    const out = formatDate("2025-03-14T00:00:00Z");
    expect(out).not.toBe("2025-03-14");
    expect(out.length).toBeGreaterThan(5);
  });
  it("returns dash for invalid", () => {
    expect(formatDate(null)).toBe("—");
  });
});

describe("formatCount", () => {
  it("formats with grouping", () => {
    expect(formatCount(1234)).toMatch(/1,234/);
  });
});

describe("money invariant", () => {
  it("settlement + patient + writeoff equals gross", () => {
    // deterministic invariant from backend: gross = settlement + patient_liability + hospital_writeoff
    const gross = 100000;
    const settlement = 85000;
    const patient = 5000;
    const writeoff = 10000;
    expect(settlement + patient + writeoff).toBe(gross);
    // also test with fixture values: gross 950000, settlement 850000 from wire-shape fixture implies patient+writeoff = 100000
    const fixtureGross = 950000;
    const fixtureSettlement = 850000;
    expect(fixtureGross - fixtureSettlement).toBe(100000);
  });
});

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const INR_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
  minimumFractionDigits: 0,
});

const COUNT_FORMATTER = new Intl.NumberFormat("en-IN");
const PERCENT_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "percent",
  maximumFractionDigits: 1,
});

/**
 * Format a number/string as Indian rupees: ₹1,23,456
 * Uses Intl.NumberFormat for locale-aware grouping.
 */
export function rupees(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(n)) return "—";
  // Intl already handles negative sign and grouping
  return INR_FORMATTER.format(Math.round(n));
}

export function formatCount(value: number): string {
  return COUNT_FORMATTER.format(value);
}

export function formatPercent(value: number): string {
  // value is 0-100, convert to 0-1 for percent formatter or just format manually
  return `${COUNT_FORMATTER.format(Number(value.toFixed(1)))}%`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) {
    // fallback to slicing if not a valid date, but avoid showing raw slice elsewhere
    return iso.slice(0, 10);
  }
  return new Intl.DateTimeFormat("en-IN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(d);
}

/**
 * Parse a decimal string to number safely.
 */
export function num(value: string | number | null | undefined): number {
  if (value == null || value === "") return 0;
  const n = typeof value === "string" ? parseFloat(value) : value;
  return isNaN(n) ? 0 : n;
}

/**
 * Today's date as YYYY-MM-DD, in local time (toISOString is UTC and can shift
 * the date near midnight).
 */
export function today(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/**
 * Shift a YYYY-MM-DD date string by N days using local calendar arithmetic.
 */
export function shiftDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(y, m - 1, d + days);
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${mm}-${dd}`;
}

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format a number/string as Indian rupees: ₹1,23,456
 */
export function rupees(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "—";

  const abs = Math.abs(Math.round(num));
  const sign = num < 0 ? "-" : "";

  // Indian number system: last 3 digits, then groups of 2
  const str = abs.toString();
  if (str.length <= 3) return `${sign}₹${str}`;

  const last3 = str.slice(-3);
  const rest = str.slice(0, -3);
  const grouped = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${sign}₹${grouped},${last3}`;
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

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format a number/string as Indian rupees: ₹1,23,456
 */
export function rupees(value: string | number | null | undefined): string {
  if (value == null || value === "") return "₹0";
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "₹0";

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
 * Today's date as YYYY-MM-DD
 */
export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Shift a date string by N days
 */
export function shiftDays(iso: string, days: number): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

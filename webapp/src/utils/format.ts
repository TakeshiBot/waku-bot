/**
 * Number, date and value formatting.
 *
 * `Intl` covers everything needed here, so no date library. The locale comes from
 * the panel's own locale state, which follows the user's waku language setting.
 */

import { locale } from "@/i18n";

export const BOT_TIME_ZONE = "Asia/Ho_Chi_Minh";

/** Database timestamps without an offset are UTC; never interpret them in the browser zone. */
function timestampDate(iso: string): Date {
  const value = iso.trim();
  const naive = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value);
  return new Date(naive ? `${value.replace(" ", "T")}Z` : value);
}

export function formatNumber(value: number, options: Intl.NumberFormatOptions = {}): string {
  return new Intl.NumberFormat(locale.value, options).format(value);
}

/** Format a 0..1 ratio as a percentage, keeping small values legible. */
export function formatPercent(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(locale.value, {
    style: "percent",
    maximumFractionDigits,
  }).format(value);
}

/** Format an ISO timestamp as a short date and time in the bot's UTC+7 timezone. */
export function formatDateTime(iso: string): string {
  if (!iso) return "-";
  const date = timestampDate(iso);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: BOT_TIME_ZONE,
  }).format(date);
}

/** Format an ISO timestamp as a date only. */
export function formatDate(iso: string): string {
  if (!iso) return "-";
  const date = timestampDate(iso);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: "medium",
    timeZone: BOT_TIME_ZONE,
  }).format(date);
}

/** Collapse whitespace and cut to length, for list previews. */
export function truncate(text: string, length = 80): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > length ? `${collapsed.slice(0, length)}…` : collapsed;
}

const TOKEN_UNITS: ReadonlyArray<readonly [number, string]> = [
  [1_000, "k"],
  [1_000_000, "M"],
  [1_000_000_000, "B"],
  [1_000_000_000_000, "T"],
];

/**
 * Format a token count compactly.
 *
 * Token budgets run to six and seven figures, where the exact digits stop being
 * readable and stop mattering. The unit is chosen from the rounded value, so a count
 * that rounds up to 1000.0 is promoted instead of printed as "1000.0k". Below a
 * thousand the number is exact, so a small allowance still shows what it really is.
 */
export function formatTokens(value: number): string {
  if (Math.abs(value) < 1_000) return formatNumber(value);
  let scale = 1_000_000_000_000;
  let suffix = "T";
  for (const [candidate, candidateSuffix] of TOKEN_UNITS) {
    if (Math.abs(Number((value / candidate).toFixed(1))) < 1_000) {
      scale = candidate;
      suffix = candidateSuffix;
      break;
    }
  }
  return `${formatNumber(value / scale, { minimumFractionDigits: 1, maximumFractionDigits: 1, useGrouping: false })}${suffix}`;
}

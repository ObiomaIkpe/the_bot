/** Reported live: the trade-story detail page showed a Stop price as
 * `1.1577600000000001` -- ordinary binary floating-point noise from
 * upstream arithmetic (e.g. entry - stop_distance), not a data-
 * correctness problem. Every genuine price in this dataset has at
 * most 6 decimal digits (FX quotes, occasionally fractional pips), so
 * rounding to 6 decimals strips the noise without truncating any real
 * precision. Math.round (not toFixed) is used so the result is a
 * clean number-to-string conversion with no trailing zeros
 * (1.1646 stays "1.1646", not "1.164600"). */
export function formatPrice(value: number | null | undefined): string {
  if (value == null) return "-";
  return String(Math.round(value * 1e6) / 1e6);
}

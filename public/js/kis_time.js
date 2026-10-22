/* Pure KST formatting shared by monitor and strategy settings; no UI initialization. */
const kisTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});
export function kisDate(value) {
  if (value == null || value === "") return null;
  // Backend timestamps without an explicit offset are UTC as well.
  const raw = String(value).trim();
  const normalized = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(raw) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? `${raw}Z` : raw;
  const date = new Date(normalized);
  return Number.isFinite(date.getTime()) ? date : null;
}
export function formatKisTime(value) {
  const date = kisDate(value); if (!date) return "-";
  const parts = Object.fromEntries(kisTimeFormatter.formatToParts(date).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

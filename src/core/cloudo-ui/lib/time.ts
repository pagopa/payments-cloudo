const ROME_TIME_ZONE = "Europe/Rome";

const romeFormatter = new Intl.DateTimeFormat("sv-SE", {
  timeZone: ROME_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const HAS_TIME_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

// Renders "YYYY-MM-DD HH:mm:ss" in Europe/Rome. Strings without a UTC offset are
// already Rome local time (worker-side stamps), so they are only normalized.
export function formatRome(value?: string | Date | null): string {
  if (!value) return "-";
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? "-" : romeFormatter.format(value);
  }
  const raw = String(value).trim();
  if (!HAS_TIME_ZONE.test(raw)) {
    return raw.replace("T", " ").split(".")[0];
  }
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? raw : romeFormatter.format(parsed);
}

export function romeDate(value?: string | null): string {
  const formatted = formatRome(value);
  return formatted === "-" ? "" : formatted.slice(0, 10);
}

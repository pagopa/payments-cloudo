const ROME_TIME_ZONE = "Europe/Rome";

const partsFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: ROME_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

type Matcher = (value: number) => boolean;

// Mirrors the orchestrator's scheduler matcher (utils.is_cron_now): `*`, `*/n`, `a,b`, `a-b`, `n`.
function buildMatcher(part: string): Matcher | null {
  if (part === "*") return () => true;
  if (part.startsWith("*/")) {
    const step = Number(part.slice(2));
    return Number.isInteger(step) && step > 0 ? (v) => v % step === 0 : null;
  }
  if (part.includes(",")) {
    const values = part.split(",").map(Number);
    return values.some((v) => !Number.isInteger(v))
      ? null
      : (v) => values.includes(v);
  }
  if (part.includes("-")) {
    const [start, end] = part.split("-").map(Number);
    return Number.isInteger(start) && Number.isInteger(end)
      ? (v) => v >= start && v <= end
      : null;
  }
  const single = Number(part);
  return Number.isInteger(single) ? (v) => v === single : null;
}

const pad = (n: number) => String(n).padStart(2, "0");

// Next minute (Europe/Rome wall clock) at which the scheduler will fire the 6-field cron
// expression, as "YYYY-MM-DD HH:mm:ss"; null when it never fires or cannot be parsed.
export function nextCronRun(
  cron: string,
  from: Date = new Date(),
): string | null {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 6) return null;
  const matchers = parts.map(buildMatcher);
  if (matchers.some((m) => m === null)) return null;
  const [sec, min, hour, day, month, dow] = matchers as Matcher[];
  // The scheduler evaluates at second 0 of each minute.
  if (!sec(0)) return null;

  const p = Object.fromEntries(
    partsFormatter.formatToParts(from).map((x) => [x.type, x.value]),
  );
  // Wall-clock fields kept in a UTC-based Date so day/month arithmetic is DST-free.
  let cur = Date.UTC(
    Number(p.year),
    Number(p.month) - 1,
    Number(p.day),
    Number(p.hour),
    Number(p.minute) + 1,
  );

  const limit = cur + 366 * 24 * 60 * 60 * 1000;
  while (cur <= limit) {
    const d = new Date(cur);
    if (
      !month(d.getUTCMonth() + 1) ||
      !day(d.getUTCDate()) ||
      !dow(d.getUTCDay())
    ) {
      cur = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
      continue;
    }
    if (!hour(d.getUTCHours())) {
      cur = Date.UTC(
        d.getUTCFullYear(),
        d.getUTCMonth(),
        d.getUTCDate(),
        d.getUTCHours() + 1,
      );
      continue;
    }
    if (min(d.getUTCMinutes())) {
      return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(
        d.getUTCDate(),
      )} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:00`;
    }
    cur += 60 * 1000;
  }
  return null;
}

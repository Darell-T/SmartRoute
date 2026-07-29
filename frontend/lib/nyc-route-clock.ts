/**
 * Rider-facing route and arrival clocks for SmartRoute.
 *
 * NYC transit times are always rendered in America/New_York so unit tests and
 * CI runners on UTC (or any host zone) match local Eastern wall-clock display.
 * This formats server-owned ISO timestamps only; it does not invent route facts.
 */

const NYC_TIME_ZONE = "America/New_York";

const nycClockFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZone: NYC_TIME_ZONE,
});

/**
 * Format a valid ISO timestamp or epoch-ms instant as a 12-hour NYC clock.
 * Returns null for missing or invalid values (existing unavailable state).
 */
export function formatNycRouteClock(
  value: string | number | null | undefined,
): string | null {
  if (value == null || value === "") return null;
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return nycClockFormatter.format(date);
}

export { NYC_TIME_ZONE };

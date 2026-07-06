export function secondsSince(epochSeconds: number | null | undefined, nowMs: number): number {
  if (!epochSeconds) return 0;
  return Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
}

export function formatDistance(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  if (meters < 160) return `${Math.round(meters)} m`;
  return `${Math.max(0.1, meters / 1609.344).toFixed(1)} mi`;
}

export function formatWalk(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  return `${Math.max(1, Math.round(meters / 84))} min walk`;
}

const TRANSIT_ABBREVIATIONS: Record<string, string> = {
  AV: "Av",
  AVE: "Av",
  ST: "St",
  STS: "Sts",
  SQ: "Sq",
  BLVD: "Blvd",
  BL: "Bl",
  PKWY: "Pkwy",
  PKY: "Pkwy",
  STA: "Sta",
  RD: "Rd",
  DR: "Dr",
  PL: "Pl",
  PK: "Pk",
  HTS: "Hts",
  CTR: "Ctr",
  JCT: "Jct",
  TER: "Ter",
  EXPY: "Expy",
  HWY: "Hwy",
  BCH: "Bch",
  TPKE: "Tpke",
};

/* Real acronyms stay all-caps; everything else all-caps is shouting. */
const KEEP_ALL_CAPS = new Set([
  "JFK",
  "LGA",
  "SBS",
  "SIR",
  "NYC",
  "WTC",
  "LIRR",
]);

function titleCaseTransitToken(token: string): string {
  if (!token) return token;
  const upper = token.toUpperCase();
  if (token !== upper) {
    // Already mixed case — trust it, except raw GTFS "McDONALD"-style
    // tokens where only the Mc survives in lowercase.
    const mc = token.match(/^(Mc)([A-Z]{2,})$/);
    if (mc) return `Mc${mc[2].charAt(0)}${mc[2].slice(1).toLowerCase()}`;
    return token;
  }
  if (upper === "VIA") return "via";
  if (KEEP_ALL_CAPS.has(upper)) return upper;
  const bare = upper.replace(/[^A-Z0-9]/g, "");
  if (TRANSIT_ABBREVIATIONS[bare]) {
    return upper.replace(bare, TRANSIT_ABBREVIATIONS[bare]);
  }
  if (upper.length === 1) return upper; // compass letters: E 18 St, W 4 St
  const ordinal = upper.match(/^(\d+)(ST|ND|RD|TH)$/);
  if (ordinal) return `${ordinal[1]}${ordinal[2].toLowerCase()}`;
  if (/^\d/.test(upper)) return upper;
  return upper.charAt(0) + upper.slice(1).toLowerCase();
}

/* Token-wise, so "BROWNSVILLE MOTHER GASTON BL via AMBOY" cleans even
   though the lowercase "via" means the string as a whole isn't all-caps. */
function titleCaseTransitLabel(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return trimmed
    .split(/(\s+|\/|-)/)
    .map((part) => (/^\s+$|^[-/]$/.test(part) ? part : titleCaseTransitToken(part)))
    .join("");
}

export function cleanDestinationLabel(value: unknown): string {
  return titleCaseTransitLabel(String(value ?? "").replace(/\s+/g, " ").trim());
}

/* Bus headsigns pack qualifiers into the destination ("LIMITED SUNSET PARK
   3 AV via CHURCH"). The row title should be the destination alone; the
   qualifiers belong on the metadata line. */
export function splitBusHeadsign(label: string): {
  destination: string;
  qualifiers: string[];
} {
  let rest = label.trim();
  const qualifiers: string[] = [];
  const limited = rest.match(/^(?:limited|ltd\.?)\s+/i);
  if (limited) {
    qualifiers.push("Limited");
    rest = rest.slice(limited[0].length);
  }
  const via = rest.match(/\s+via\s+(.+)$/i);
  if (via && typeof via.index === "number") {
    qualifiers.push(`via ${via[1].trim()}`);
    rest = rest.slice(0, via.index).trim();
  }
  return { destination: rest, qualifiers };
}

export function minutesUntilArrival(arrivalTimeSeconds: number, nowMs: number): number {
  const deltaSeconds = arrivalTimeSeconds - nowMs / 1000;
  if (deltaSeconds < 60) return 0;
  return Math.max(1, Math.ceil(deltaSeconds / 60));
}

export function labelForMinutes(mins: number): string {
  if (mins <= 0) return "Now";
  if (mins === 1) return "1 min";
  return `${mins} min`;
}

export function labelForArrivalMinutes(minutes: number[]): string {
  const values = minutes.slice(0, 3);
  if (values.length <= 1) return labelForMinutes(values[0] ?? 0);
  const rendered = values.map((mins) => (mins <= 0 ? "Now" : String(mins)));
  return `${rendered.join(", ")} min`.replace("Now min", "Now");
}

export function minutesAgo(epochSeconds: number | null | undefined, nowMs: number): string {
  if (!epochSeconds) return "live";
  const minutes = Math.max(0, Math.round((nowMs / 1000 - epochSeconds) / 60));
  if (minutes < 1) return "now";
  if (minutes === 1) return "1m";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

export function formatClockAt(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

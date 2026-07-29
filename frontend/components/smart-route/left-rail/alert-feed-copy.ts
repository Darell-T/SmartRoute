const ROUTE_TOKEN_PATTERN = /\[([A-Za-z0-9+-]{1,8})\]/g;
const INLINE_ICON_PLACEHOLDER_PATTERN =
  /\[(?:free\s+)?(?:shuttle\s+bus|bus|subway|train|shuttle)\s+icon\]|\[(?:shuttle\s+bus|bus|subway|train|shuttle)\]/gi;
const NON_TERMINAL_TAIL =
  /(?:\b(?:St|Av|Ave|Avs|Rd|Blvd|Sq|Pkwy|Hwy|Ct|Ft|Mt|Jct|Dr|Ln|Pl|Terr|No|approx|vs)|\b[A-Z])\.$/;

export function cleanPassengerAlertText(value: string | undefined): string {
  return String(value ?? "")
    .replace(INLINE_ICON_PLACEHOLDER_PATTERN, " ")
    .replace(/[Â·â€¢]/g, " - ")
    .replace(/[â†’â†”]/g, " and ")
    .replace(/â€“|â€”/g, "-")
    .replace(/â‰ˆ/g, "about")
    .replace(/what'?s happening\??:?/gi, " ")
    .replace(/planned work reminder:?/gi, " ")
    .replace(/what to expect:?/gi, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\s+-\s+$/g, "");
}

export function deriveLifecycle(
  text: string,
): "active" | "monitoring" | "resolved" {
  const value = cleanPassengerAlertText(text).toLowerCase();
  if (
    /resolved|resumed|returned to normal|back to normal|restored|cleared|no longer|has ended|good service/.test(
      value,
    )
  ) {
    return "resolved";
  }
  if (
    /investigat|monitoring|being addressed|we are addressing|crews are|response en route|on scene|awaiting/.test(
      value,
    )
  ) {
    return "monitoring";
  }

  return "active";
}

export function splitSentences(value: string | undefined): string[] {
  const text = cleanPassengerAlertText(value);
  if (!text) {
    return [];
  }

  const sentences: string[] = [];
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character !== "." && character !== "!" && character !== "?") {
      continue;
    }

    const next = text[index + 1];
    if (next !== undefined && next !== " ") {
      continue;
    }

    const sentence = text.slice(start, index + 1).trim();
    if (character === "." && NON_TERMINAL_TAIL.test(sentence)) {
      continue;
    }
    if (sentence) {
      sentences.push(sentence);
    }
    start = index + 1;
  }

  const tail = text.slice(start).trim();
  if (tail) {
    sentences.push(tail);
  }

  return sentences;
}

export function leadSentences(
  value: string | undefined,
  count = 2,
  maxChars = 240,
): string | undefined {
  if (!value) {
    return undefined;
  }

  const sentences = splitSentences(value);
  if (sentences.length === 0) {
    return undefined;
  }

  let output = "";
  for (const sentence of sentences.slice(0, count)) {
    const candidate = output ? `${output} ${sentence}` : sentence;
    if (output && candidate.length > maxChars) {
      break;
    }
    output = candidate;
  }
  if (output.length > maxChars) {
    output = `${output.slice(0, maxChars).replace(/\s+\S*$/, "").trim()}â€¦`;
  }

  return output || undefined;
}

export function compactAlertTitle(
  title: string,
  routeIds: string[] = [],
  fallback = "Service alert",
): string {
  const cleaned = cleanPassengerAlertText(title);
  if (!cleaned) {
    return fallback;
  }

  const withoutSource = cleaned.replace(/^MTA\s+/i, "").trim();
  const withoutTokens = withoutSource.replace(ROUTE_TOKEN_PATTERN, "$1").trim();
  const routePattern = routeIds.length
    ? new RegExp(
        `\\b(?:${routeIds.map(escapeRegExp).join("|")})\\b\\s*(?:\\/\\s*\\b(?:${routeIds.map(escapeRegExp).join("|")})\\b\\s*)?trains?$`,
        "i",
      )
    : null;

  if (/person needed medical attention|medical assistance/i.test(withoutTokens)) {
    return titleWithAt(
      withoutTokens.replace(/person needed medical attention/i, "Medical assistance"),
    );
  }
  if (/partial suspension/i.test(withoutTokens)) {
    return withoutTokens
      .replace(/\s*-\s*/g, " between ")
      .replace(/\s+and\s+and\s+/i, " and ");
  }

  const runningDelays = withoutTokens.match(
    /\btrains?\s+(?:are\s+)?running with delays\b(.*)$/i,
  );
  if (runningDelays) {
    return sentenceCase(`Delays${runningDelays[1]}`.trim());
  }

  const noService = withoutTokens.match(
    /\bthere is no (?:[A-Za-z0-9/ ]{1,12}\s)?service (?:in either direction )?(between [A-Za-z0-9 .'\-\/]+?)(?:[.,]|$)/i,
  );
  if (noService) {
    return sentenceCase(`No service ${noService[1].trim()}`);
  }

  const everyMinutes = withoutTokens.match(
    /\bruns?\s+(?:about\s+)?every\s+(\d+)\s+minutes?\b([^.,]*)/i,
  );
  if (everyMinutes) {
    return `Runs every ${everyMinutes[1]} minutes${everyMinutes[2]
      .replace(/\s+/g, " ")
      .trimEnd()}`;
  }
  if (/\b(?:trains?\s+(?:are\s+)?running|runs?)\s+express\b/i.test(withoutTokens)) {
    return "Running express";
  }
  if (/\b(?:trains?\s+(?:are\s+)?running|runs?)\s+local\b/i.test(withoutTokens)) {
    return "Running local";
  }
  if (/\bskip(?:s|ping)?\b/i.test(withoutTokens) && !/suspension/i.test(withoutTokens)) {
    return "Skipping stations";
  }
  if (/\badditional\b[^.]*\bservice\b/i.test(withoutTokens)) {
    return "Additional service";
  }
  if (routePattern?.test(withoutTokens)) {
    return "Trains running with delays";
  }

  const leadStripped = withoutSource
    .replace(/^(?:\[[A-Za-z0-9+-]{1,4}\]\s*)+/, "")
    .trim();
  if (
    leadStripped !== withoutSource &&
    /^(?:runs?|trains?|service|is|are|will|has|have|no)\b/i.test(leadStripped) &&
    leadStripped.split(/\s+/).length >= 3
  ) {
    return sentenceCase(leadStripped);
  }

  return sentenceCase(withoutSource);
}

export function compactAlertSummary(
  summary: string | undefined,
  title = "",
): string | undefined {
  const cleaned = cleanPassengerAlertText(summary);
  if (
    !cleaned ||
    cleaned.toLowerCase() === cleanPassengerAlertText(title).toLowerCase()
  ) {
    return undefined;
  }

  return cleaned
    .replace(/^MTA\s+/i, "")
    .replace(/\bALL trains\b/g, "All trains")
    .replace(/btwn/gi, "between")
    .replace(/\s+/g, " ")
    .trim();
}

export function compactAlertTimestamp(value: string | undefined): string {
  const text = cleanPassengerAlertText(value).toLowerCase();
  if (!text || text === "just now") {
    return "now";
  }

  return text === "live" ? "live" : text.replace(/\s+ago$/, "");
}

export function compactFeedTitle(title: string, routeIds: string[]): string {
  const cleaned = cleanPassengerAlertText(title);
  const [kindRaw, placeRaw] = cleaned.split(/\s+-\s+/);
  const kind = sentenceCase(kindRaw || cleaned);
  const place = cleanPassengerAlertText(placeRaw);
  if (/medical/i.test(kind) && place) {
    return `Medical assistance at ${place}`;
  }
  if (/fire response/i.test(kind) && place) {
    return `Fire response at ${place}`;
  }
  if (/police activity/i.test(kind) && place) {
    return `Police activity near ${place}`;
  }
  if (/stalled/i.test(kind)) {
    return routeIds[0] ? `Stalled ${routeIds[0]} train` : "Stalled train";
  }
  if (/partial suspension/i.test(kind) && place) {
    return `Partial suspension between ${place}`;
  }

  return place ? `${kind} at ${place}` : kind;
}

export function parseAlertAlternatives(
  value: string | undefined,
  estClear: string | undefined,
): string | undefined {
  const picks = splitSentences(cleanPassengerAlertText(value))
    .filter(
      (sentence) =>
        /^for alternative service/i.test(sentence) ||
        /\b(?:[Uu]se|[Tt]ake)\s+(?:the\s+)?(?:\[?[A-Z0-9]|nearby|free|shuttle)/.test(
          sentence,
        ),
    )
    .slice(0, 2);
  if (picks.length > 0) {
    return picks.join(" ");
  }

  const clearedAt = cleanPassengerAlertText(estClear).replace(/^~\s*/, "");
  return clearedAt && clearedAt !== "-"
    ? `Expected to clear around ${clearedAt}.`
    : undefined;
}

export function alertSlug(value: string): string {
  return cleanPassengerAlertText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
}

export function sentenceCase(value: string): string {
  const text = value.trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function titleWithAt(text: string): string {
  return cleanPassengerAlertText(text)
    .replace(/\s+at\s+/i, " at ")
    .replace(/\s+near\s+/i, " near ");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

import { z } from "zod";

import type {
  CanonicalItinerary,
  CanonicalItineraryLeg,
  CanonicalItineraryPlace,
} from "./agent-route-card-contract";

const canonicalLabelFields = z.object({
  display_name: z.string().nullable().optional(),
  label: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  address: z.string().nullable().optional(),
  station_name: z.string().nullable().optional(),
});

function parsedLabel(value: CanonicalItineraryLeg["board"]): string | null {
  const stringValue = z.string().safeParse(value);
  if (stringValue.success) return stringValue.data.trim() || null;

  const fields = canonicalLabelFields.safeParse(value);
  if (!fields.success) return null;
  for (const candidate of [
    fields.data.display_name,
    fields.data.label,
    fields.data.name,
    fields.data.address,
    fields.data.station_name,
  ]) {
    const label = candidate?.trim();
    if (label) return label;
  }
  return null;
}

export function canonicalPlaceLabel(
  place: CanonicalItinerary["origin"] | CanonicalItineraryPlace,
  fallback: string,
): string {
  return parsedLabel(place) ?? fallback;
}

export function canonicalStopLabel(
  stop: CanonicalItineraryLeg["board"],
): string | null {
  return parsedLabel(stop);
}

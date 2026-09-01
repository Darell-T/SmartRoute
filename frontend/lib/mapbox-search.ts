import type { DestinationSelection } from "@/types";

const NYC_BBOX = "-74.2591,40.4774,-73.7004,40.9176";
const NYC_PROXIMITY = "-73.9857,40.7484";

export type MapboxSearchSuggestion = {
  id: string;
  label: string;
  address?: string;
  mapboxId?: string;
  coordinates?: {
    lat: number;
    lng: number;
  };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function coordinatesFromUnknown(value: unknown): { lat: number; lng: number } | undefined {
  if (!isRecord(value)) return undefined;
  const latitude = finiteNumber(value.latitude);
  const longitude = finiteNumber(value.longitude);
  if (latitude === null || longitude === null) return undefined;
  return { lat: latitude, lng: longitude };
}

function suggestionFromUnknown(entry: unknown): MapboxSearchSuggestion[] {
  if (!isRecord(entry)) return [];
  const mapboxId = optionalText(entry.mapbox_id);
  const label = destinationLabel(
    optionalText(entry.name),
    optionalText(entry.full_address) || optionalText(entry.place_formatted),
  );
  const coordinates = coordinatesFromUnknown(entry.coordinates);
  if (!mapboxId && !coordinates) return [];
  return [
    {
      id: mapboxId || label,
      label,
      address: optionalText(entry.full_address) || optionalText(entry.place_formatted),
      mapboxId,
      coordinates,
    },
  ];
}

function parseSuggestResponse(value: unknown): MapboxSearchSuggestion[] {
  if (!isRecord(value) || !Array.isArray(value.suggestions)) return [];
  return value.suggestions.flatMap(suggestionFromUnknown);
}

function selectionFromSuggestion(suggestion: MapboxSearchSuggestion): DestinationSelection | null {
  if (!suggestion.coordinates) return null;
  return {
    label: suggestion.label,
    address: suggestion.address,
    coordinates: suggestion.coordinates,
  };
}

function parseRetrieveFeature(
  value: unknown,
  fallbackLabel: string,
): DestinationSelection | null {
  if (!isRecord(value) || !Array.isArray(value.features)) return null;
  const feature = value.features[0];
  if (!isRecord(feature) || !isRecord(feature.geometry) || !Array.isArray(feature.geometry.coordinates)) {
    return null;
  }
  const lng = finiteNumber(feature.geometry.coordinates[0]);
  const lat = finiteNumber(feature.geometry.coordinates[1]);
  if (lat === null || lng === null) return null;
  const properties = isRecord(feature.properties) ? feature.properties : {};
  const address =
    optionalText(properties.full_address) || optionalText(properties.place_formatted);
  return {
    label: destinationLabel(optionalText(properties.name), address, fallbackLabel),
    address,
    coordinates: { lat, lng },
  };
}

export function createMapboxSearchSessionToken() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function destinationLabel(name?: string, place?: string, fallback = "Destination") {
  const cleanName = name?.trim();
  const cleanPlace = place?.trim();
  if (cleanName && cleanPlace && !cleanPlace.startsWith(cleanName)) {
    return `${cleanName}, ${cleanPlace}`;
  }
  return cleanName || cleanPlace || fallback;
}

export async function suggestMapboxPlaces({
  query,
  accessToken,
  sessionToken,
  signal,
}: {
  query: string;
  accessToken: string;
  sessionToken: string;
  signal?: AbortSignal;
}): Promise<MapboxSearchSuggestion[]> {
  const params = new URLSearchParams({
    q: query,
    access_token: accessToken,
    session_token: sessionToken,
    country: "us",
    language: "en",
    limit: "5",
    proximity: NYC_PROXIMITY,
    bbox: NYC_BBOX,
  });

  const response = await fetch(
    `https://api.mapbox.com/search/searchbox/v1/suggest?${params.toString()}`,
    { signal },
  );
  if (!response.ok) return [];
  return parseSuggestResponse(await response.json());
}

export async function retrieveMapboxSuggestion({
  suggestion,
  accessToken,
  sessionToken,
}: {
  suggestion: MapboxSearchSuggestion;
  accessToken: string;
  sessionToken: string;
}): Promise<DestinationSelection | null> {
  const known = selectionFromSuggestion(suggestion);
  if (known) return known;
  if (!suggestion.mapboxId) return null;

  const params = new URLSearchParams({
    access_token: accessToken,
    session_token: sessionToken,
  });
  const response = await fetch(
    `https://api.mapbox.com/search/searchbox/v1/retrieve/${encodeURIComponent(
      suggestion.mapboxId,
    )}?${params.toString()}`,
  );
  if (!response.ok) return null;

  const retrieved = parseRetrieveFeature(await response.json(), suggestion.label);
  if (!retrieved) return null;
  return {
    label: retrieved.label,
    address: retrieved.address || suggestion.address,
    coordinates: retrieved.coordinates,
  };
}

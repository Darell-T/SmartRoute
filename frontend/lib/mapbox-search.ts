import { z } from "zod";

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

const optionalSearchTextSchema = z
  .string()
  .refine((value) => value.trim().length > 0)
  .optional()
  .catch(undefined);

const finiteSearchNumberSchema = z
  .number()
  .refine((value) => Number.isFinite(value))
  .optional()
  .catch(undefined);

const mapboxCoordinatesSchema = z
  .object({
    latitude: finiteSearchNumberSchema,
    longitude: finiteSearchNumberSchema,
  })
  .passthrough()
  .transform((coords) => {
    if (coords.latitude === undefined || coords.longitude === undefined) return undefined;
    return { lat: coords.latitude, lng: coords.longitude };
  })
  .optional()
  .catch(undefined);

const mapboxSuggestionEntrySchema = z
  .object({
    mapbox_id: optionalSearchTextSchema,
    name: optionalSearchTextSchema,
    full_address: optionalSearchTextSchema,
    place_formatted: optionalSearchTextSchema,
    coordinates: mapboxCoordinatesSchema,
  })
  .passthrough()
  .transform((entry): MapboxSearchSuggestion[] => {
    const mapboxId = entry.mapbox_id;
    const label = destinationLabel(
      entry.name,
      entry.full_address || entry.place_formatted,
    );
    const coordinates = entry.coordinates;
    if (!mapboxId && !coordinates) return [];
    return [
      {
        id: mapboxId || label,
        label,
        address: entry.full_address || entry.place_formatted,
        mapboxId,
        coordinates,
      },
    ];
  })
  .catch([]);

const mapboxSuggestResponseSchema = z
  .object({
    suggestions: z.array(mapboxSuggestionEntrySchema),
  })
  .passthrough()
  .transform((payload) => payload.suggestions.flat())
  .catch([]);

const mapboxRetrievePropertiesSchema = z
  .object({
    name: optionalSearchTextSchema,
    full_address: optionalSearchTextSchema,
    place_formatted: optionalSearchTextSchema,
  })
  .passthrough()
  .catch({});

const mapboxRetrieveResponseSchema = z
  .object({
    features: z.array(z.unknown()),
  })
  .passthrough();

const mapboxRetrieveFeatureSchema = z
  .object({
    geometry: z
      .object({
        coordinates: z.array(z.unknown()),
      })
      .passthrough(),
    properties: mapboxRetrievePropertiesSchema.optional(),
  })
  .passthrough();

function selectionFromSuggestion(suggestion: MapboxSearchSuggestion): DestinationSelection | null {
  if (!suggestion.coordinates) return null;
  return {
    label: suggestion.label,
    address: suggestion.address,
    coordinates: suggestion.coordinates,
  };
}

export function createMapboxSearchSessionToken() {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && "randomUUID" in cryptoObj) {
    return cryptoObj.randomUUID();
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
  return mapboxSuggestResponseSchema.parse(await response.json());
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

  const retrievedResponse = mapboxRetrieveResponseSchema.safeParse(await response.json());
  if (!retrievedResponse.success) return null;
  const feature = mapboxRetrieveFeatureSchema.safeParse(retrievedResponse.data.features[0]);
  if (!feature.success) return null;
  const lng = finiteSearchNumberSchema.parse(feature.data.geometry.coordinates[0]);
  const lat = finiteSearchNumberSchema.parse(feature.data.geometry.coordinates[1]);
  if (lat === undefined || lng === undefined) return null;
  const properties = feature.data.properties ?? {};
  const address = properties.full_address || properties.place_formatted;
  return {
    label: destinationLabel(properties.name, address, suggestion.label),
    address: address || suggestion.address,
    coordinates: { lat, lng },
  };
}

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

type SearchBoxSuggestionResponse = {
  suggestions?: Array<{
    mapbox_id?: string;
    name?: string;
    full_address?: string;
    place_formatted?: string;
    feature_type?: string;
    coordinates?: {
      latitude?: number;
      longitude?: number;
    };
  }>;
};

type SearchBoxRetrieveResponse = {
  features?: Array<{
    properties?: {
      name?: string;
      full_address?: string;
      place_formatted?: string;
    };
    geometry?: {
      coordinates?: [number, number];
    };
  }>;
};

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

  const data = (await response.json()) as SearchBoxSuggestionResponse;
  return (data.suggestions ?? [])
    .map((suggestion) => {
      const longitude = suggestion.coordinates?.longitude;
      const latitude = suggestion.coordinates?.latitude;
      const label = destinationLabel(
        suggestion.name,
        suggestion.full_address || suggestion.place_formatted,
      );
      return {
        id: suggestion.mapbox_id || label,
        label,
        address: suggestion.full_address || suggestion.place_formatted,
        mapboxId: suggestion.mapbox_id,
        coordinates:
          typeof latitude === "number" && typeof longitude === "number"
            ? { lat: latitude, lng: longitude }
            : undefined,
      };
    })
    .filter((suggestion) => suggestion.mapboxId || suggestion.coordinates);
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
  if (suggestion.coordinates) {
    return {
      label: suggestion.label,
      address: suggestion.address,
      coordinates: suggestion.coordinates,
    };
  }

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

  const data = (await response.json()) as SearchBoxRetrieveResponse;
  const feature = data.features?.[0];
  const coordinates = feature?.geometry?.coordinates;
  if (!coordinates) return null;

  const [lng, lat] = coordinates;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;

  const properties = feature.properties;
  return {
    label: destinationLabel(
      properties?.name,
      properties?.full_address || properties?.place_formatted,
      suggestion.label,
    ),
    address:
      properties?.full_address || properties?.place_formatted || suggestion.address,
    coordinates: { lat, lng },
  };
}

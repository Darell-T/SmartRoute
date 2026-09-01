import assert from "node:assert/strict";
import test from "node:test";

import {
  createMapboxSearchSessionToken,
  retrieveMapboxSuggestion,
  suggestMapboxPlaces,
} from "./mapbox-search.ts";

function mockFetch(impl) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  return () => {
    globalThis.fetch = original;
  };
}

test("suggestMapboxPlaces returns no suggestions when Mapbox is unavailable", async () => {
  const restore = mockFetch(async () => new Response("nope", { status: 503 }));
  try {
    const suggestions = await suggestMapboxPlaces({
      query: "Costco",
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.deepEqual(suggestions, []);
  } finally {
    restore();
  }
});

test("suggestMapboxPlaces returns parsed suggestions from a valid Mapbox payload", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        suggestions: [
          {
            mapbox_id: "mbx_1",
            name: "Costco",
            full_address: "976 3rd Ave, Brooklyn",
            coordinates: { latitude: 40.65, longitude: -74.01 },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const suggestions = await suggestMapboxPlaces({
      query: "Costco",
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.deepEqual(suggestions, [
      {
        id: "mbx_1",
        label: "Costco, 976 3rd Ave, Brooklyn",
        address: "976 3rd Ave, Brooklyn",
        mapboxId: "mbx_1",
        coordinates: { lat: 40.65, lng: -74.01 },
      },
    ]);
  } finally {
    restore();
  }
});

test("suggestMapboxPlaces ignores malformed entries instead of inventing coordinates", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        suggestions: [{ name: "Nowhere" }, { mapbox_id: "mbx_ok", name: "OK" }],
      }),
      { status: 200 },
    ),
  );
  try {
    const suggestions = await suggestMapboxPlaces({
      query: "place",
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(suggestions.length, 1);
    assert.equal(suggestions[0].mapboxId, "mbx_ok");
    assert.equal(suggestions[0].coordinates, undefined);
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion uses coordinates already on the suggestion", async () => {
  const restore = mockFetch(async () => {
    throw new Error("retrieve must not fetch when coordinates are known");
  });
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: {
        id: "mbx_1",
        label: "Costco",
        address: "976 3rd Ave",
        coordinates: { lat: 40.65, lng: -74.01 },
      },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.deepEqual(selection, {
      label: "Costco",
      address: "976 3rd Ave",
      coordinates: { lat: 40.65, lng: -74.01 },
    });
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null for a malformed retrieve payload", async () => {
  const restore = mockFetch(async () =>
    new Response(JSON.stringify({ features: [{ geometry: { coordinates: ["bad"] } }] }), {
      status: 200,
    }),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection, null);
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null when Mapbox times out or errors", async () => {
  const restore = mockFetch(async () => new Response("nope", { status: 504 }));
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection, null);
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null when the suggestion has neither coordinates nor a mapbox id", async () => {
  const selection = await retrieveMapboxSuggestion({
    suggestion: { id: "local", label: "Nowhere" },
    accessToken: "pk.test",
    sessionToken: "sess",
  });
  assert.equal(selection, null);
});

test("suggestMapboxPlaces ignores a non-object payload and incomplete coordinates", async () => {
  const restore = mockFetch(async (url) => {
    if (String(url).includes("suggest")) {
      return new Response(JSON.stringify({ suggestions: [null, { coordinates: { latitude: 40.7 } }] }), {
        status: 200,
      });
    }
    return new Response("[]", { status: 200 });
  });
  try {
    assert.deepEqual(
      await suggestMapboxPlaces({ query: "x", accessToken: "pk.test", sessionToken: "sess" }),
      [],
    );
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null when the feature list is missing", async () => {
  const restore = mockFetch(async () => new Response(JSON.stringify({}), { status: 200 }));
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection, null);
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion hydrates coordinates from a valid retrieve feature", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        features: [
          {
            properties: { name: "JFK", full_address: "Jamaica, NY" },
            geometry: { coordinates: [-73.7781, 40.6413] },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_jfk", label: "JFK", mapboxId: "mbx_jfk" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.deepEqual(selection, {
      label: "JFK, Jamaica, NY",
      address: "Jamaica, NY",
      coordinates: { lat: 40.6413, lng: -73.7781 },
    });
  } finally {
    restore();
  }
});

test("createMapboxSearchSessionToken falls back when randomUUID is unavailable", () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: {} });
  try {
    assert.match(createMapboxSearchSessionToken(), /^sr-/);
  } finally {
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
  }
});

test("suggestMapboxPlaces uses the label as id when Mapbox omits mapbox_id", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        suggestions: [
          {
            name: "Union Sq",
            coordinates: { latitude: 40.7359, longitude: -73.9906 },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const suggestions = await suggestMapboxPlaces({
      query: "Union",
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(suggestions[0].id, "Union Sq");
    assert.equal(suggestions[0].mapboxId, undefined);
  } finally {
    restore();
  }
});

test("suggestMapboxPlaces ignores a payload whose suggestions field is not a list", async () => {
  const restore = mockFetch(async () =>
    new Response(JSON.stringify({ suggestions: { name: "Costco" } }), { status: 200 }),
  );
  try {
    assert.deepEqual(
      await suggestMapboxPlaces({ query: "Costco", accessToken: "pk.test", sessionToken: "sess" }),
      [],
    );
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null when the first feature is not an object", async () => {
  const restore = mockFetch(async () =>
    new Response(JSON.stringify({ features: [null] }), { status: 200 }),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection, null);
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion keeps the suggestion address when retrieve omits one", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        features: [
          {
            properties: { name: "Costco" },
            geometry: { coordinates: [-74.01, 40.65] },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", address: "976 3rd Ave", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection.address, "976 3rd Ave");
    assert.deepEqual(selection.coordinates, { lat: 40.65, lng: -74.01 });
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion uses the suggestion label when retrieve omits names", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        features: [
          {
            properties: {},
            geometry: { coordinates: [-73.99, 40.75] },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Times Square", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection.label, "Times Square");
    assert.deepEqual(selection.coordinates, { lat: 40.75, lng: -73.99 });
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion still resolves when feature properties are missing", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        features: [
          {
            properties: null,
            geometry: { coordinates: [-73.99, 40.75] },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Times Square", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection.label, "Times Square");
    assert.deepEqual(selection.coordinates, { lat: 40.75, lng: -73.99 });
  } finally {
    restore();
  }
});

test("retrieveMapboxSuggestion returns null when coordinates are not finite", async () => {
  const restore = mockFetch(async () =>
    new Response(
      JSON.stringify({
        features: [
          {
            properties: { name: "Costco" },
            geometry: { coordinates: ["west", 40.65] },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  try {
    const selection = await retrieveMapboxSuggestion({
      suggestion: { id: "mbx_1", label: "Costco", mapboxId: "mbx_1" },
      accessToken: "pk.test",
      sessionToken: "sess",
    });
    assert.equal(selection, null);
  } finally {
    restore();
  }
});

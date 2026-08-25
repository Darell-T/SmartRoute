type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

type AgentRequest = {
  message?: string;
  response_presentation?: string;
};

const ORIGIN = { label: "Times Square", lat: 40.758, lng: -73.9855 };
const DESTINATION = { label: "Atlantic Av-Barclays Center", lat: 40.6844, lng: -73.9777 };

const plannedRoute = {
  card_id: "release-route-1",
  role: "recommended",
  origin: ORIGIN,
  destination: DESTINATION,
  summary: {
    eta_minutes: 31,
    transfers: 0,
    lines: ["2", "3"],
    reason: "The fastest option with no transfers.",
  },
  route: [
    {
      type: "WALK",
      departure_stop: "Times Square",
      arrival_stop: "Times Sq-42 St",
      minutes_until_arrival: 3,
      start_point: { latitude: 40.758, longitude: -73.9855 },
      end_point: { latitude: 40.7553, longitude: -73.987 },
    },
    {
      type: "SUBWAY",
      train_line: "2",
      line_color: "#EE352E",
      departure_stop: "Times Sq-42 St",
      arrival_stop: "Atlantic Av-Barclays Ctr",
      minutes_until_arrival: 28,
      stop_count: 8,
      departure_coords: { latitude: 40.7553, longitude: -73.987 },
      arrival_coords: { latitude: 40.6844, longitude: -73.9777 },
    },
  ],
  alerts: [],
  itinerary: {
    itinerary_id: "release-itinerary-1",
    total_duration_seconds: 1860,
    transfer_count: 0,
    legs: [
      {
        mode: "WALK",
        board: "Times Square",
        alight: "Times Sq-42 St",
        walk_seconds: 180,
      },
      {
        mode: "SUBWAY",
        service_id: "2",
        board: "Times Sq-42 St",
        alight: "Atlantic Av-Barclays Ctr",
        stop_count: 8,
        ride_seconds: 1680,
      },
    ],
  },
};

function successTurn(turnId: string, text: string, extras: StreamEvent[] = []): StreamEvent[] {
  return [
    { event: "meta", data: { session_id: "release-session", turn_id: turnId } },
    { event: "token", data: { text } },
    ...extras,
    {
      event: "done",
      data: {
        session_id: "release-session",
        turn_id: turnId,
        stop_reason: "end_turn",
        terminal_state: "completed",
        usage: { input_tokens: 12, output_tokens: 8 },
      },
    },
  ];
}

export function eventsForRequest(request: AgentRequest): StreamEvent[] {
  const message = request.message?.trim().toLowerCase() ?? "";
  const turnId = `release-${message.replace(/[^a-z0-9]+/g, "-") || "turn"}`;

  if (message === "hello") {
    return successTurn(turnId, "Good morning — where are you headed?");
  }
  if (message === "fare") {
    return successTurn(turnId, "A subway ride is $3.00.");
  }
  if (message === "plan") {
    return successTurn(turnId, "Take the 2 or 3 for a direct trip.", [
      { event: "route_card", data: { turn_id: turnId, ...plannedRoute } },
    ]);
  }
  if (message === "arrival") {
    return successTurn(turnId, "Here are the next arrivals at Times Sq-42 St.", [
      {
        event: "arrival_card",
        data: {
          turn_id: turnId,
          route_id: "2",
          stop: {
            id: "127",
            name: "Times Sq-42 St",
            distance_meters: 120,
            latitude: 40.7553,
            longitude: -73.987,
          },
          directions: [
            {
              id: "downtown",
              label: "Downtown",
              arrivals: [
                {
                  expected_at: "2026-07-28T09:04:00-04:00",
                  minutes: 4,
                  realtime: true,
                  trip_id: null,
                  vehicle_id: null,
                },
              ],
            },
          ],
          updated_at: "2026-07-28T09:00:00-04:00",
          source_status: "live",
          resolution_status: "resolved",
        },
      },
    ]);
  }
  if (message === "fail") {
    return [
      { event: "meta", data: { session_id: "release-session", turn_id: turnId } },
      {
        event: "error",
        data: { code: "upstream_error", message: "The test provider is temporarily unavailable.", retryable: true },
      },
      {
        event: "done",
        data: {
          session_id: "release-session",
          turn_id: turnId,
          stop_reason: "error",
          terminal_state: "failed",
          usage: { input_tokens: 3, output_tokens: 0 },
        },
      },
    ];
  }
  if (message === "retry") {
    return successTurn(turnId, "Retry succeeded with grounded local test data.");
  }
  return successTurn(turnId, `Received deterministic request: ${message}`);
}

export function sseBody(events: StreamEvent[]): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

# Backend package map

Find the `backend/app` module that owns a behavior.
The request path and owner diagram live in [`backend/ARCHITECTURE.md`](../../backend/ARCHITECTURE.md).
Regenerate the module list with `rg --files backend/app -g "*.py"`.

## App root

- `backend/app/main.py`. Loads environment and configuration, starts shared clients, registers routers, and exposes `health` and `readiness`. Imported by tests only.
- `backend/app/observability.py`. Owns turn and tool telemetry through `start_turn`, `finish_turn`, and `wrap_anthropic`. Production importers use `observability`.
- `backend/app/runtime.py`. Owns runtime-profile checks and `env_int` / `env_float`. Production importers use `runtime`.

## Routers

- `backend/app/routers/agent_chat.py`. Owns `POST /api/agent/chat` SSE transport through `agent_chat`. Production importers use `agent_chat`.
- `backend/app/routers/incident_refresh.py`. Internal cron entrypoint for background incident intelligence. Production importers use `incident_refresh`.
- `backend/app/routers/live_feed/router.py`. Owns the HTTP and WebSocket routes for live feed, service alerts, and vehicles. Production importers use `close_background_bus_tasks`, `router`, `ws_router`.
- `backend/app/routers/live_feed/socket.py`. WebSocket message validation and streaming coordination for live feed. Production importers use `socket`.
- `backend/app/routers/live_feed/ticket.py`. WebSocket ticket verification for the live-feed transport. Production importers use `ticket`.
- `backend/app/routers/subway.py`. Owns `GET /api/subway-stops` through `subway_stops`. Production importers use `subway`.
- `backend/app/routers/trips.py`. Direct REST trip planning for the Live Map surface. Production importers use `trips`.

## Services root

- `backend/app/services/admission.py`. Shared Redis admission with orphan-safe, expiring lease membership. Production importers use `admission`.
- `backend/app/services/cache.py`. Owns Redis-backed `cache_get` and `cache_set` with an in-memory fallback. Production importers use `cache`, `cache_get`, `cache_set`.
- `backend/app/services/directions.py`. Owns Google Routes candidate fetching for trip preparation. Production importers use `directions`.
- `backend/app/services/evidence.py`. Small, shared freshness contract for model- and scoring-facing evidence. Production importers use `evidence`, `EvidenceEnvelope`, `current_payload`, `evidence_envelope`, `parse_timestamp`.
- `backend/app/services/geography.py`. Owns NYC geocoding, `is_in_nyc`, `distance_meters`, and `find_nearest_stops`. Production importers use `distance_meters`, `geography`, `NYC_BOUNDS`, `find_nearest_stops`.
- `backend/app/services/parsing.py`. Owns `finite_float` and `nonnegative_int`. Production importers use `finite_float`, `nonnegative_int`.
- `backend/app/services/text.py`. Rider-facing text sanitization for trip narration. Production importers use `text`, `collapse_whitespace`.

## Agent

- `backend/app/services/agent/candidate_store.py`. Server-owned route candidate sets for outer-agent selection. Production importers use `candidate_store`.
- `backend/app/services/agent/discovery_store.py`. Server-owned, session-scoped discovery sets and place references. Production importers use `discovery_store`, `normalize_price_level`.
- `backend/app/services/agent/events.py`. SSE event types streamed to the frontend by the conversational agent. Production importers use `events`, `ProgressStage`, `ProgressStatus`.
- `backend/app/services/agent/loop.py`. Public facade for the conversational agent's turn lifecycle. Production importers use `loop`.
- `backend/app/services/agent/passenger_output.py`. Rider-language safety for activity labels, framing, and recovery copy. Production importers use `framed_events`, `validated_framing`, `MAX_PRESENTATION_FRAMING_CHARS`, `MAX_RESEARCH_PRESENTATION_FRAMING_CHARS`, `SUSPICIOUS_RIDER_TEXT`, `passenger_output`.
- `backend/app/services/agent/presented_entity_registry.py`. Bounded memory for places the rider has actually seen. Production importers use `presented_entity_registry`.
- `backend/app/services/agent/profile.py`. Small, explicit session-scoped profile fallback for conversational turns. Production importers use `profile`.
- `backend/app/services/agent/public_surface.py`. Stable vocabulary with a small, state-valid tool surface per round. Production importers use `public_surface`, `offered_custom_tools`.
- `backend/app/services/agent/session.py`. Owns conversational session state, leases, and pending continuations. Production importers use `session`.
- `backend/app/services/agent/tool_input_policy.py`. Validate and normalize model capability inputs before execution. Production importers use `validated_goal_key`, `authoritative_discovery_input`, `constrained_tool_input`, `goal_error`, `missing_verified_destination`, `rider_excluded_modes`.
- `backend/app/services/agent/transcript_store.py`. Complete rider-visible conversation storage for one agent session. Production importers use `transcript_store`.
- `backend/app/services/agent/trip_state.py`. Validated, session-owned conversational trip and scenario state. Production importers use `trip_state`.

## Agent model

- `backend/app/services/agent/model/budget.py`. Cost + concurrency guardrails for the conversational agent. Production importers use `budget`.
- `backend/app/services/agent/model/mock_turn.py`. Deterministic local-development turn fixture for the conversational agent. Production importers use `mock_turn`.
- `backend/app/services/agent/model/output_projection.py`. Projects model-visible place and route values through `project_model_value` and `project_presented_route`. Production importers use `opaque_place_id`, `project_model_value`, `project_place_point`, `project_presented_route`, `project_tool_result_data`.
- `backend/app/services/agent/model/policy.py`. Central response-mode policy for the conversational SmartRoute agent. Production importers use `policy`.
- `backend/app/services/agent/model/prompt.py`. Owns `SINGLE_AGENT_SYSTEM_PROMPT` and `build_turn_context`. Production importers use `prompt`.
- `backend/app/services/agent/model/request.py`. Anthropic request construction, diagnostics, and retry classification. Production importers use `request`.
- `backend/app/services/agent/model/stream.py`. Immediate model-round streaming with bounded server-tool progress. Production importers use `stream`.

## Agent turn

- `backend/app/services/agent/turn/completion.py`. Pure completion decision plus bounded continuation persistence. Production importers use `completion`.
- `backend/app/services/agent/turn/contract.py`. Immutable declarations for the outcomes of one agent turn. Production importers use `GoalState`, `GoalKind`, `TurnContract`, `ContractValidationError`, `OutcomeGoal`.
- `backend/app/services/agent/turn/evidence.py`. Turn-local evidence, obligations, and terminal enforcement. Production importers use `TurnEvidence`.
- `backend/app/services/agent/turn/finalization.py`. Turn finalization, development trace, and safe terminal telemetry. Production importers use `record_phase_ms`, `TurnTrace`, `extract_safe_usage`, `finalize_trace`, `finalize_turn`, `record_capability_attempts`.
- `backend/app/services/agent/turn/ledger.py`. Per-turn tool execution accounting for the conversational agent. Production importers use `TurnToolLedger`, `ToolProgressRelay`, `run_one_tool`.
- `backend/app/services/agent/turn/stream.py`. Deadline-bound live-model turn execution for the conversational agent. Production importers use `stream`.
- `backend/app/services/agent/turn/tool_round.py`. Tool input policy and one parallel model-tool round for an agent turn. Production importers use `ToolRoundResultMessage`, `TurnDeadlineReachedError`, `execute_tool_round`, `mixed_terminal_and_capability`, `tool_round`.

## Agent tools

- `backend/app/services/agent/tools/__init__.py`. Tool registry for the rider-facing conversational transit agent. Production importers use `ToolContext`, `ToolResult`, `COMBINED_TOOL_REGISTRY`, `ToolSpec`.
- `backend/app/services/agent/tools/base.py`. Shared types for agent tool executors. Production importers use `ToolContext`, `ToolResult`, `ToolOutcome`.
- `backend/app/services/agent/tools/complete_turn.py`. Terminal conversational answer, clarification, refusal, or unavailable outcome. Production importers use `complete_turn`.
- `backend/app/services/agent/tools/declare_goals.py`. Model tool for declaring rider outcomes before capability execution. Production importers use `declare_goals`.
- `backend/app/services/agent/tools/location_resolution.py`. Agent-owned location resolution for named and discovered endpoints. Production importers use `ResolvedPlace`, `parse_coordinates`, `resolve_named_place`, `resolve_named_point`, `resolve_destination_reference`, `resolve_waypoint_places`.
- `backend/app/services/agent/tools/provider_http.py`. Shared single-request JSON fetch for tools that hit one REST endpoint. Production importers use `fetch_json`.

## Agent tools places

- `backend/app/services/agent/tools/places/damn_lines.py`. Optional Damn Lines queue evidence for exact Google Places venues. Production importers use `damn_lines`.
- `backend/app/services/agent/tools/places/discover_places.py`. Owns the `discover_places` tool schema and `execute`. Production importers use `discover_places`.
- `backend/app/services/agent/tools/places/geography.py`. Maps `discover_places` scope values onto canonical NYC borough geography. Production importers use `geography`.
- `backend/app/services/agent/tools/places/place_reference.py`. Server-owned place-reference resolution for conversational discovery. Production importers use `place_reference`.
- `backend/app/services/agent/tools/places/present_places.py`. Owns the `present_places` tool schema, `execute`, and `try_deterministic_fallback`. Production importers use `present_places`.
- `backend/app/services/agent/tools/places/search_local_places.py`. Owns Google Places search for discovery, including `execute` and `provider_search`. Production importers use `search_local_places`.

## Agent tools route

- `backend/app/services/agent/tools/route/preparation_adapter.py`. Agent adapters for the shared route-preparation implementation. Production importers use `PreparedLeg`, `build_preparation_dependencies`, `new_preparation_timings`, `prepare_single_leg`.
- `backend/app/services/agent/tools/route/prepare_route_branches.py`. Server-owned destination-branch resolution and route preparation. Production importers use `aggregate_destination_ids`, `is_current_location_discovery`, `limit_final_branch_chains`, `prepare_destination_branches`, `reasonable_branch_indexes`, `resolve_destination_options`.
- `backend/app/services/agent/tools/route/prepare_route_options.py`. Prepare server-owned route candidates for one outer conversational model. Production importers use `prepare_route_options`.
- `backend/app/services/agent/tools/route/prepare_route_persistence.py`. Persist and project one finalized route candidate set. Production importers use `bind_canonical_destination_identities`, `nonfatal_prepare_result`, `persist_route_candidates`.
- `backend/app/services/agent/tools/route/present_route.py`. Present one validated server-owned route candidate as the route card. Production importers use `present_route`.
- `backend/app/services/agent/tools/route/present_route_commit.py`. Reserve, commit, and record a validated route presentation. Production importers use `record_presentation`, `reserve_and_commit`.
- `backend/app/services/agent/tools/route/present_route_state.py`. Load and validate the immutable candidate facts owned by present_route. Production importers use `ValidatedRoutePresentation`, `is_destination_comparison`, `canonical_facts`, `destination_selection_mode`, `owned_candidate`, `rebind_to_entry`.
- `backend/app/services/agent/tools/route/route_input.py`. Agent adapter for route input and session/profile preference composition. Production importers use `merge_route_preparation_input`, `point_label`, `summary_eta_minutes`, `validated_waypoints`.
- `backend/app/services/agent/tools/route/route_projection.py`. Canonical route projection for present_route. Production importers use `project_canonical_route`.

## Agent tools transit

- `backend/app/services/agent/tools/transit/accessibility_status.py`. Owns MTA elevator and escalator status lookup through `execute`. Production importers use `accessibility_status`.
- `backend/app/services/agent/tools/transit/check_area_conditions.py`. Bounded current-condition evidence for one rider-named NYC area. Production importers use `check_area_conditions`.
- `backend/app/services/agent/tools/transit/check_transit.py`. Owns the `check_transit` tool schema and `execute`. Production importers use `check_transit`.
- `backend/app/services/agent/tools/transit/direction.py`. Bounded semantic direction handling for transit evidence. Production importers use `normalize_direction`, `resolve_direction`, `DirectionResolution`, `accepted_trip_direction`, `direction_clarification`, `direction_matches`.
- `backend/app/services/agent/tools/transit/evidence.py`. Compact, server-owned evidence produced by the existing transit tools. Production importers use `evidence`.
- `backend/app/services/agent/tools/transit/evidence_binding.py`. Bind provider transit evidence to entities in the accepted itinerary. Production importers use `evidence_binding`.
- `backend/app/services/agent/tools/transit/evidence_matching.py`. Route, direction, concern, and coverage matching for transit evidence. Production importers use `normalized_route_ids`, `normalized_text`, `arrival_coverage`, `concern_match`, `concerns`, `confirmed`.
- `backend/app/services/agent/tools/transit/evidence_projection.py`. Passenger-safe projection helpers for typed transit evidence. Production importers use `accessibility_text`, `arrivals_text`, `operation_facts`, `operation_facts_text`, `renderable_arrival_card`, `row_direction`.
- `backend/app/services/agent/tools/transit/evidence_store.py`. Bounded persistence for server-owned transit evidence sets. Production importers use `TransitEvidenceSet`, `load_evidence_set`, `new_evidence_set_id`, `store_evidence_set`.
- `backend/app/services/agent/tools/transit/lookup_arrivals.py`. Public dispatcher for deterministic subway and bus arrival lookups. Production importers use `lookup_arrivals`.
- `backend/app/services/agent/tools/transit/lookup_arrivals_bus.py`. BusTime-backed bus arrival lookup. Production importers use `execute`.
- `backend/app/services/agent/tools/transit/lookup_arrivals_common.py`. Shared normalization and payload shaping for arrival providers. Production importers use `canonical_station_query`, `FEED_STALE_AFTER_S`, `ARRIVAL_LIMIT_DEFAULT`, `ARRIVAL_LIMIT_MAX`, `BOARDING_BUFFER_MINUTES`, `assess_catchability`.
- `backend/app/services/agent/tools/transit/lookup_arrivals_subway.py`. GTFS-realtime subway arrival lookup. Production importers use `execute`.
- `backend/app/services/agent/tools/transit/lookup_facts.py`. Owns the `lookup_facts` tool and the local `transit_facts.md` digest. Production importers use `lookup_facts`.
- `backend/app/services/agent/tools/transit/present_transit.py`. Facade for presenting one server-owned transit evidence set. Production importers use `present_transit`.
- `backend/app/services/agent/tools/transit/transit_snapshot.py`. Owns transit snapshot collection through `execute` and `collect_service_status`. Production importers use `collect_service_status`, `transit_snapshot`.
- `backend/app/services/agent/tools/transit/venue_crowd_window.py`. Hidden-agent adapter for neutral venue crowd-window facts. Production importers use `venue_crowd_window`.

## Trips

- `backend/app/services/trips/candidates.py`. Build deterministic route candidates and rider-facing comparison copy. Production importers use `candidates`.
- `backend/app/services/trips/direct_plan.py`. Direct Live Map trip planning: shared preparation, deterministic selection. Production importers use `direct_plan`.
- `backend/app/services/trips/enrichment.py`. GTFS + bus leg enrichment for a single route. Production importers use `enrichment`.
- `backend/app/services/trips/itinerary.py`. Canonical itinerary normalizer. Production importers use `TRANSIT_MODES`, `build_canonical_itinerary`, `build_chained_itinerary`, `DEFAULT_DWELL_MINUTES`.
- `backend/app/services/trips/location.py`. Neutral location value objects and model-free named-place resolution. Production importers use `ResolvedPlace`, `canonical_display_name`, `KnownPlace`, `known_place`, `parse_coordinates`, `resolve_named_place`.
- `backend/app/services/trips/scoring.py`. Route scoring + route-step accessors. Production importers use `scoring`.
- `backend/app/services/trips/selection_decision.py`. Grounded route-candidate evaluation and deterministic fallback choice. Production importers use `evaluate_candidate_decision`, `evaluate_dominated_selection`, `select_fallback_candidate`.
- `backend/app/services/trips/selection_record.py`. Canonical persisted record for a completed route-selection decision. Production importers use `build_route_selection_decision`.
- `backend/app/services/trips/transfer_semantics.py`. Normalize transit transfer movement before scoring and projection. Production importers use `route_accessibility`, `normalize_routes`, `route_walking_totals`, `route_transfer_facts`.

## Trips crowds

- `backend/app/services/trips/crowds/event.py`. Associate neutral event-provider facts with concrete route candidates. Production importers use `event`.
- `backend/app/services/trips/crowds/event_provider.py`. Ticketmaster event facts and neutral venue/event normalization. Production importers use `event_provider`.
- `backend/app/services/trips/crowds/evidence.py`. Provider-neutral collection and verification of route crowd evidence. Production importers use `evidence`.
- `backend/app/services/trips/crowds/hotspots.py`. Curated event clusters associated with complete candidate transit paths. Production importers use `HotspotHit`, `hotspots`.
- `backend/app/services/trips/crowds/search.py`. Five-minute cache boundary for bounded Grok crowd research. Production importers use `search`.
- `backend/app/services/trips/crowds/search_normalization.py`. Validate and normalize untrusted Grok crowd-search output. Production importers use `parse_json`, `response_text`, `normalize_search_payload`.
- `backend/app/services/trips/crowds/search_provider.py`. Bounded async Grok transport for route-scoped crowd research. Production importers use `close_crowd_search_client`, `run_search`.

## Trips preparation

- `backend/app/services/trips/preparation/combine.py`. Combine prepared legs into index-aligned route candidate chains. Production importers use `combine_prepared_chains`.
- `backend/app/services/trips/preparation/constraints.py`. Canonical route constraints and passenger-safe candidate digests. Production importers use `route_constraints`, `ROUTE_STATUSES`, `candidate_digest`, `route_status`.
- `backend/app/services/trips/preparation/context.py`. Neutral boundaries for the shared route-preparation pipeline. Production importers use `RoutePreparationFailure`, `RoutePreparationContext`, `is_route_preparation_failure`.
- `backend/app/services/trips/preparation/dependencies.py`. Neutral preparation-only bindings for the shared prepare_single_leg. Production importers use `EVENT_EVIDENCE_TTL_S`, `LIVE_EVIDENCE_TTL_S`, `TRIP_CONTEXT_TIMEOUT_S`, `build_preparation_dependencies`, `dependencies`, `new_preparation_timings`.
- `backend/app/services/trips/preparation/evidence.py`. Evidence scoping and coverage helpers for prepared route options. Production importers use `candidate_evidence_for_route`, `coverage_for_prepared`, `merge_candidate_evidence`, `merge_coverage`, `merge_event_status`, `merge_evidence_envelopes`.
- `backend/app/services/trips/preparation/finalize.py`. Bind prepared route rows to one immutable canonical evidence snapshot. Production importers use `finalize_aggregate`.
- `backend/app/services/trips/preparation/input.py`. Neutral route-request validation and provider-recovery helpers. Production importers use `normalize_route_ids`, `derive_arrive_by_departure`, `parse_rfc3339`, `route_with_recovery`, `prepare_structural_candidates`, `point_label`.
- `backend/app/services/trips/preparation/multi_stop.py`. Bounded, candidate-dependent preparation for ordered multi-stop trips. Production importers use `prepare_multi_stop`.
- `backend/app/services/trips/preparation/prepare.py`. Gather, enrich, and score route candidates without nested model selection. Production importers use `AggregatePreparation`, `PreparedChain`, `PreparedLeg`, `PreparationDependencies`, `prepare_single_leg`.

## Trips route incidents

- `backend/app/services/trips/route_incidents/association.py`. Bounded candidate evidence associations shared by incident handoff paths. Production importers use `attach_verified_match_association`, `normalize_matcher_association`, `_CANDIDATE_ROUTE_ID`.
- `backend/app/services/trips/route_incidents/context.py`. Candidate-aware stop context for local incident searches. Production importers use `CandidateStopContext`, `CandidateStopAssociation`, `extract_candidate_stop_context`, `valid_coordinate_pair`.
- `backend/app/services/trips/route_incidents/index_adapter.py`. Pure context matching and projection for the incident-index lookup. Production importers use `extract_lookup_context`, `project_records`.
- `backend/app/services/trips/route_incidents/matching.py`. Deterministic local matching of cached 511NY incidents to route stops. Production importers use `match_cached_incidents`, `_as_mapping`.
- `backend/app/services/trips/route_incidents/merge.py`. Conservative, source-aware incident evidence filtering and merging. Production importers use `filter_current_incidents`, `merge_incident_evidence`.
- `backend/app/services/trips/route_incidents/scan.py`. Candidate-scoped incident evidence from the deterministic incident index. Production importers use `incident_scan_is_complete`, `INCOMPLETE_INCIDENT_DISCLOSURE`, `contains_unsafe_incident_clear`, `scan`.

## Live feed

- `backend/app/services/live_feed/network_snapshot.py`. Process-owned normalized MTA realtime state. Production importers use `network_snapshot_store`, `NetworkSnapshot`.
- `backend/app/services/live_feed/snapshot.py`. Builds a rider-scoped live snapshot through `build_live_snapshot`. Production importers use `build_live_snapshot`, `snapshot`.
- `backend/app/services/live_feed/vehicle_enrichment.py`. Place a train along its trip stop sequence when GTFS-RT omits coordinates. Production importers use `vehicle_enrichment`.

## Incidents

- `backend/app/services/incidents/batches.py`. Coarse NYC geographic batches for the background incident job. Production importers use `IncidentBatch`, `INCIDENT_BATCHES`, `coverage_batch_ids_for_point`.
- `backend/app/services/incidents/evidence.py`. Pure validation for cited incident evidence shared by scanning and caching. Production importers use `canonical_citation_url`, `source_identity_from_url`, `source_type_matches_url`.
- `backend/app/services/incidents/index.py`. Deterministic cache-backed incident index and coverage metadata. Production importers use `index`.
- `backend/app/services/incidents/normalization.py`. Normalization and deterministic identity for incident records. Production importers use `bounded_text`, `bounded_ids`, `bounded_int`, `incident_id_for`, `sanitize_source_records`, `ALLOWED_COVERAGE`.
- `backend/app/services/incidents/ny511.py`. Process-local 511NY event snapshots. Production importers use `NY511Settings`, `SnapshotStore`.
- `backend/app/services/incidents/official.py`. Official incident normalization and bounded source snapshots. Production importers use `OfficialIncidentSnapshot`, `SOURCE_ALERTS`, `SOURCE_GTFS_RT`, `STATUS_CURRENT`, `STATUS_PARTIAL`, `STATUS_UNAVAILABLE`.
- `backend/app/services/incidents/refresh.py`. Thirty-minute background incident refresh orchestration (cron only). Production importers use `refresh`.
- `backend/app/services/incidents/scout.py`. Bounded background Grok X scouting with conditional Web corroboration. Production importers use `ScoutBatchResult`, `scout_incident_batch`.
- `backend/app/services/incidents/scout_normalization.py`. Pure parsing and evidence normalization for the background incident scout. Production importers use `SIX_HOURS`, `build_incident_inputs`, `is_valid_web_payload`, `is_valid_x_payload`, `normalize_web_corroborations`, `normalize_x_claims`.
- `backend/app/services/incidents/scout_provider.py`. Optional xAI transport for the bounded background incident scout. Production importers use `ScoutSearchResult`, `close_incident_scout_client`, `has_client`, `sanitized_claims`, `_run_web_search`, `_run_x_search`.

## MTA

- `backend/app/services/mta/alerts.py`. Owns service-alert fetch and `parse_service_alerts`. Production importers use `project_service_alert`, `is_material_service_alert`, `fetch_service_alerts`, `parse_service_alerts`, `alerts`, `filter_alerts_for_routes`.
- `backend/app/services/mta/bus.py`. Owns BusTime stop monitoring and bus vehicle helpers. Production importers use `bus`, `fetch_bus_route_stop_groups`, `fetch_bus_stop_monitoring`, `fetch_nearby_bus_stops`, `get_stalled_buses`, `parse_bus_stop_monitoring`.
- `backend/app/services/mta/bus_runtime.py`. Lifecycle-owned transport and bounded sharing for BusTime requests. Production importers use `bus_runtime`, `close_bus_client`, `start_bus_client`.
- `backend/app/services/mta/bus_updates.py`. Bounded BusTime arrival updates for live-feed consumers. Production importers use `BusUpdate`, `BusUpdateData`, `BusUpdateEvent`, `cached_nearby_bus_update`, `fetch_nearby_bus_update`.
- `backend/app/services/mta/config.py`. Owns NYC timezone, subway route colors, and `route_to_feed`. Production importers use `NYC_TZ`, `route_to_feed`, `ALL_SUBWAY_ROUTES`, `ALERTS_URL`, `BASE_URL`, `BUS_STOPS_FOR_LOCATION_URL`.
- `backend/app/services/mta/feeds.py`. Owns GTFS-realtime feed fetch and `parse_feed_message`. Production importers use `fetch_feeds_with_metadata`, `parse_bytes`, `parse_feed_message`, `fetch_feeds`, `_gtfs_realtime_pb2`.
- `backend/app/services/mta/realtime.py`. Concrete realtime MTA service interface. Production importers use `realtime`.
- `backend/app/services/mta/static_gtfs/migration.py`. Owns static GTFS Postgres migration helpers. Production importers use `migrate`.
- `backend/app/services/mta/static_gtfs/scheduled_arrivals.py`. Owns the scheduled-arrival index for static GTFS. Production importers use `ScheduledArrivalIndex`.
- `backend/app/services/mta/static_gtfs/stop_patterns.py`. Owns stop-pattern lookup and `normalize_station_name`. Production importers use `normalize_station_name`, `StopPatternIndex`.
- `backend/app/services/mta/static_gtfs/store.py`. Bounded intermediate-stop cache lives with the static-pattern store. Production importers use `GTFSStaticData`, `close_pool`, `init_pool`.
- `backend/app/services/mta/subway.py`. Owns subway vehicle-position construction from GTFS-realtime. Production importers use `build_subway_vehicle_positions`, `detect_stalled_trains`, `get_stalled_trains`, `parse_vehicle_positions`, `subway`.

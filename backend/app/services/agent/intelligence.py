"""Small deterministic intent helpers that must not depend on model behavior."""

from __future__ import annotations

import ast
import dataclasses
import operator
import re
from typing import Literal

SmartRouteIntent = Literal[
    "route_planning",
    "destination_discovery",
    "arrival_lookup",
    "area_conditions",
    "simple_general",
    "transit_question",
    "unsupported",
]

_CROWD_RE = re.compile(
    r"\b(?:(?:avoid|less|fewer)\s+(?:the\s+)?(?:crowd(?:s|ed)?|busy\s+stations?|"
    r"event\s+traffic|concert\s+crowds?|game\s+traffic|theater\s+crowds?|"
    r"protests?|parades?|rallies?|street\s+(?:conditions?|fairs?))|"
    r"(?:check|research|look\s+for|account\s+for)\s+(?:nearby\s+)?(?:crowds?|"
    r"events?|concerts?|protests?|parades?|rallies?|street\s+conditions?))\b",
    re.IGNORECASE,
)
_ARRIVAL_NOUN_RE = re.compile(r"\b(?:arrivals?|departures?)\b", re.IGNORECASE)
_ARRIVAL_VEHICLE_RE = re.compile(r"\b(?:train|subway|bus|one)\b", re.IGNORECASE)
_ARRIVAL_TIMING_RE = re.compile(
    r"\b(?:next|coming|how\s+long|when|show|make|catch|after\s+this)\b",
    re.IGNORECASE,
)
_IMPLICIT_ARRIVAL_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+coming\s+next|what\s+comes\s+after\s+this\s+one|"
    r"when\s+is\s+my\s+(?:train|bus)|next\s+(?:train|bus|one)|"
    r"how\s+long\s+until\s+(?:it|the\s+(?:train|bus))|"
    r"will\s+i\s+(?:make|catch)\s+(?:the\s+)?next)\b",
    re.IGNORECASE,
)
_ROUTE_ID_RE = re.compile(
    r"\b(?:(?:M|B|Q|S|X)\d{1,3}s?|"
    r"[1234567]|(?<!['’])[ABCDEFGJLMNQRSWZ])\b",
    re.IGNORECASE,
)
_ROUTE_ID_TOKEN = (
    r"(?:SIM\d{1,3}(?:-SBS|\+)?|BX\d{1,3}(?:-SBS|\+)?|BM\d{1,3}(?:-SBS|\+)?|"
    r"QM\d{1,3}(?:-SBS|\+)?|(?:M|B|Q|S|X)\d{1,3}(?:-SBS|\+)?|"
    r"[1234567ABCDEFGJLMNQRSWZ])"
)
_REQUESTED_ROUTE_RE = re.compile(
    rf"\b(?:take|use|using|via|ride|prefer|want|need)\s+(?:the\s+)?"
    rf"(?P<after_action>{_ROUTE_ID_TOKEN})"
    rf"(?:\s+(?:train|subway|bus|line|route))?\b|"
    rf"\b(?P<before_noun>{_ROUTE_ID_TOKEN})\s+"
    rf"(?:train|subway|bus|line|route)\b",
    re.IGNORECASE,
)
_EXCLUDED_ROUTE_RE = re.compile(
    rf"\b(?:avoid(?:ing)?|without|no|skip(?:ping)?|don'?t\s+take|"
    rf"not\s+(?:take|taking|ride|riding|use|using)|exclude(?:ing)?)\s+"
    rf"(?:the\s+)?(?P<after_verb>{_ROUTE_ID_TOKEN})"
    rf"(?:\s+(?:train|subway|bus|line|route|service))?\b|"
    rf"\b(?P<before_noun>{_ROUTE_ID_TOKEN})\s+(?:train|subway|bus|line|route)"
    rf"(?:\s+is)?\s+(?:out|closed|skipped|not\s+running|canceled|cancelled)\b",
    re.IGNORECASE,
)
_ALLOWED_ROUTE_RE = re.compile(
    rf"\b(?:please\s+)?allow\s+(?:the\s+)?(?P<allow_route>{_ROUTE_ID_TOKEN})"
    rf"(?:\s+(?:train|subway|bus|line|route|service))?\b|"
    rf"\b(?P<state_route>{_ROUTE_ID_TOKEN})"
    rf"(?:\s+(?:train|subway|bus|line|route))?\s+is\s+"
    rf"(?:ok(?:ay)?|fine|allowed)\s+(?:now|again)\b",
    re.IGNORECASE,
)
_NEGATED_ALLOWANCE_RE = re.compile(
    r"\b(?:do\s+not|don'?t|never|not|can'?t|cannot|won'?t)\s+allow\b",
    re.IGNORECASE,
)
# Meta/status allowance guard: auxiliary questions about whether or why a
# route is allowed ("Do you allow the Q?", "Why did you allow the Q?")
# never relax; ordinary polite requests ("Can you allow the Q?") do.
_META_ALLOWANCE_RE = re.compile(
    rf"\b(?:do|does|did)\s+you\s+allow\b",
    re.IGNORECASE,
)
_NEW_TRIP_RE = re.compile(
    r"\b(?:get|take|route|directions?|head(?:ing)?|travel|trip)\b.{0,32}\b(?:to|from)\b|"
    r"\b(?:go|going)\s+to\b|"
    r"^\s*from\s+(?:my\s+)?(?:home|work)\s+to\s+(?:my\s+)?(?:home|work)[.!?]*\s*$",
    re.IGNORECASE,
)
_CONTEXTUAL_ROUTE_RE = re.compile(
    r"\b(?:route|take|get|bring)\s+(?:me|us)\s+(?:there|to\s+it|that\s+way)\b|"
    r"\bhow\s+(?:do|can|should)\s+(?:i|we)\s+get\s+there\b|"
    r"\b(?:show|give)\s+(?:me|us)\s+(?:the\s+)?(?:route|directions?)\s+there\b|"
    r"\blet'?s\s+(?:go|head|travel)\s+(?:there|to\b)",
    re.IGNORECASE,
)
_COLLOQUIAL_DESTINATION_RE = re.compile(
    r"\blet'?s\s+(?:get|grab)\s+(?:some\s+)?\S+",
    re.IGNORECASE,
)
_SAVED_PLACE_TRIP_RE = re.compile(
    r"\b(?:take|get|bring)\s+(?:me|us)?\s+(?:home|to\s+home|to\s+work)\b|"
    r"\bfrom\s+(?:home|work)\s+to\b|"
    r"\b(?:leave|start|depart)\s+from\s+(?:home|work)\b|"
    r"\bsame\s+trip\b|"
    r"\b(?:need|require)\s+(?:the\s+)?(?!(?-i:a|an)\b)(?:" + _ROUTE_ID_TOKEN + r")\b",
    re.IGNORECASE,
)
_EXPLICIT_SAVED_ENDPOINT_RE = re.compile(
    r"\b(?:take|get|bring)\s+(?:me|us)?\s+(?:home|to\s+home|to\s+work)\b|"
    r"\bfrom\s+(?:home|work)\s+to\b|"
    r"\b(?:leave|start|depart)\s+from\s+(?:home|work)\b",
    re.IGNORECASE,
)
_SLANG_ROUTE_REQUEST_RE = re.compile(
    r"^\s*(?:please\s+)?(?:gimme|give\s+me|get\s+me|"
    r"can\s+you\s+(?:give|get)\s+me)\s+(?:a\s+)?"
    r"(?:ride|route|directions?)\s+(?:to|from)\s+\S",
    re.IGNORECASE,
)
_DISCOVERY_RE = re.compile(
    r"\b(?:find|recommend|suggest(?:ion)?s?|best|good|great|where\s+should\s+i|"
    r"in\s+the\s+mood\s+for)\b.{0,72}\b(?:pizza|pancakes?|breakfast|brunch|"
    r"restaurant|food|eat|dinner|lunch|bar|cafe|coffee|bakery|dessert|"
    r"museum|park|show|venue|place|somewhere|go)\b",
    re.IGNORECASE,
)
_ROUTE_FOLLOWUP_RE = re.compile(
    r"\b(?:what\s+if|for\s+(?:this|that)\s+trip|"
    r"take\s+me\s+(?:back\s+)?home|add\s+(?:a\s+)?(?:stop|waypoint)|"
    r"(?:avoid|without|no)\s+(?:buses?|subways?|stairs?|walking|"
    r"transfers?|crowds?)|(?:less|fewer|shorter)\s+(?:walking|"
    r"transfers?|walk)|(?:leave|depart|arrive)\s+(?:at|by|later|earlier)|"
    rf"(?:avoid|without|no|skip)\s+(?:the\s+)?{_ROUTE_ID_TOKEN}"
    rf"(?:\s+(?:train|subway|bus|line|route|service))?|"
    r"change\s+(?:the\s+)?(?:route|trip|stop))\b",
    re.IGNORECASE,
)
_AREA_CONDITIONS_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+happening|is\s+it\s+safe|"
    r"(?:check|show|are\s+there|is\s+there|any)\s+(?:current\s+)?(?:conditions?|incidents?|"
    r"police\s+activity|fires?|emergencies?|shootings?|barricades?|closures?|"
    r"protests?|parades?|rallies?|events?|crowds?))\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))(?:\s+(?:there|smartroute))?[!. ]*",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(r"(?:thanks|thank you|thx)(?:\s+(?:so much|smartroute))?[!. ]*", re.IGNORECASE)
_HELP_RE = re.compile(
    r"(?:help|what can you do|how (?:do|does) (?:this|smartroute) work)[?.! ]*",
    re.IGNORECASE,
)
_OFF_TOPIC_RE = re.compile(
    r"(?:tell me a joke|write (?:me )?(?:a )?(?:poem|story)|who won (?:the )?(?:game|election)|translate .+)[?.! ]*",
    re.IGNORECASE,
)
_TRANSIT_FACT_RE = re.compile(
    r"\b(?:fare|cost|price|pay|payment|omny|metrocard|transfer\s+rules?|"
    r"service\s+hours?|overnight\s+service|bike\s+rules?)\b|\bhow\s+much\b",
    re.IGNORECASE,
)
_TRANSIT_STATUS_RE = re.compile(
    r"\b(?:running|delays?|delayed|status|suspended|suspension|service\s+change|"
    r"service\s+problem|closures?|closed|skipping|how\s+bad|what(?:'s|\s+is)\s+up\s+with)\b"
    r"|\bhow\s+is\b.{0,32}\b(?:line|train|bus|trip)\b.{0,16}\b(?:doing|looking)\b",
    re.IGNORECASE,
)
_TRANSIT_ACCESSIBILITY_RE = re.compile(
    r"\b(?:accessible|accessibility|elevators?|escalators?|wheelchairs?|"
    r"strollers?|step[- ]free|stairs?)\b",
    re.IGNORECASE,
)
_TRANSIT_EVENT_RE = re.compile(
    r"\b(?:events?|concerts?|games?|venues?|crowds?|crowded|busy|barclays|madison\s+square\s+garden|"
    r"yankee\s+stadium|citi\s+field)\b",
    re.IGNORECASE,
)
_CURRENT_REPORTING_RE = re.compile(
    r"\b(?:news|reporting|reported|reports?|articles?|latest\s+coverage)\b",
    re.IGNORECASE,
)
_SUPPORTED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_SUPPORTED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# Bounded canonical route ids: subway letters/numbers, provider bus ids
# (BX12, BM1, QM5, SIM1) with optional "-SBS" or "+" suffix, capped at
# MAX_ROUTE_ID_LENGTH so tool payloads never receive unbounded ids.
MAX_ROUTE_ID_LENGTH = 12
MAX_NORMALIZED_ROUTE_IDS = 16
_ROUTE_ID_SHAPE_RE = re.compile(r"^[A-Z0-9]{1,8}(?:-SBS|\+)?$")


@dataclasses.dataclass(frozen=True)
class RequiredEvidence:
    routes: bool = False
    service_alerts: bool = False
    places: bool = False
    events: bool = False
    arrivals: bool = False

    def required_tools(self) -> tuple[str, ...]:
        tools: list[str] = []
        if self.places:
            tools.append("search_local_places")
        if self.routes:
            tools.extend(("prepare_route_options", "present_route"))
        if self.arrivals:
            tools.append("lookup_arrivals")
        # Event collection for routes is owned by preparation so evidence is
        # associated with actual candidates before deterministic scoring.
        return tuple(tools)


@dataclasses.dataclass(frozen=True)
class ParsedIntent:
    intent: SmartRouteIntent
    avoid_crowds: bool
    arrival: "ArrivalIntent" = dataclasses.field(
        default_factory=lambda: ArrivalIntent(requested=False)
    )
    requested_route_ids: tuple[str, ...] = ()
    excluded_route_ids: tuple[str, ...] = ()
    allowed_route_ids: tuple[str, ...] = ()
    what_if: bool = False

    @property
    def arrival_route_id(self) -> str | None:
        return self.arrival.route_id

    @property
    def arrival_stop_query(self) -> str | None:
        return self.arrival.stop_query

    @property
    def required_evidence(self) -> RequiredEvidence:
        if self.intent == "arrival_lookup":
            return RequiredEvidence(arrivals=True)
        if self.intent == "destination_discovery":
            return RequiredEvidence(places=True, events=self.avoid_crowds)
        if self.intent == "route_planning":
            return RequiredEvidence(routes=True, service_alerts=True, events=self.avoid_crowds)
        return RequiredEvidence()


@dataclasses.dataclass(frozen=True)
class ArrivalIntent:
    requested: bool
    route_id: str | None = None
    stop_query: str | None = None
    direction_query: str | None = None
    use_active_trip: bool = False
    include_multiple_arrivals: bool = True
    catchability_requested: bool = False
    confidence: float = 0.0


def parse_intent(message: str) -> ParsedIntent:
    text = " ".join(str(message or "").split())
    what_if = is_what_if_request(message)
    avoid_crowds = bool(_CROWD_RE.search(text))
    arrival = parse_arrival_intent(text)
    if arrival.requested:
        return ParsedIntent(
            "arrival_lookup",
            avoid_crowds,
            arrival=arrival,
            what_if=what_if,
        )
    if evaluate_simple_arithmetic(text) is not None:
        return ParsedIntent("simple_general", avoid_crowds, what_if=what_if)
    if _GREETING_RE.fullmatch(text) or _THANKS_RE.fullmatch(text) or _HELP_RE.fullmatch(text):
        return ParsedIntent("simple_general", avoid_crowds, what_if=what_if)
    if _OFF_TOPIC_RE.fullmatch(text):
        return ParsedIntent("unsupported", avoid_crowds, what_if=what_if)
    if _DISCOVERY_RE.search(text):
        return ParsedIntent("destination_discovery", avoid_crowds, what_if=what_if)
    if _COLLOQUIAL_DESTINATION_RE.search(text):
        # This wording can resolve a new place or refer to one already present
        # in conversation. The discovery policy exposes the bounded place and
        # route tools needed for the primary model to decide from context.
        return ParsedIntent("destination_discovery", avoid_crowds, what_if=what_if)
    excluded_route_ids = extract_excluded_route_ids(text)
    excluded_set = set(excluded_route_ids)
    allowed_route_ids = extract_allowed_route_ids(text)
    allowed_set = set(allowed_route_ids)
    requested_route_ids = tuple(
        route_id
        for route_id in _requested_route_ids(text)
        if route_id not in excluded_set and route_id not in allowed_set
    )
    if (
        _ROUTE_FOLLOWUP_RE.search(text)
        or _has_explicit_route_request(text)
        or allowed_route_ids
        or _NEW_TRIP_RE.search(text)
        or _CONTEXTUAL_ROUTE_RE.search(text)
        or _SAVED_PLACE_TRIP_RE.search(text)
        or _SLANG_ROUTE_REQUEST_RE.search(text)
    ):
        return ParsedIntent(
            "route_planning",
            avoid_crowds,
            requested_route_ids=requested_route_ids,
            excluded_route_ids=excluded_route_ids,
            allowed_route_ids=allowed_route_ids,
            what_if=what_if,
        )
    if _AREA_CONDITIONS_RE.search(text):
        return ParsedIntent("area_conditions", avoid_crowds, what_if=what_if)
    return ParsedIntent("transit_question", avoid_crowds, what_if=what_if)


def transit_question_tool_names(message: object) -> frozenset[str]:
    """Return the smallest grounded tool set for a general transit question.

    ``transit_question`` is the fallback intent family, not authorization to
    expose every conversational tool. Facets may combine for legitimate
    multi-intent questions, while an explanation of an accepted route needs
    no provider tool because its canonical facts are already in turn context.
    """

    text = " ".join(str(message or "").split())
    names: set[str] = set()
    if _TRANSIT_FACT_RE.search(text):
        names.add("lookup_facts")
    if _TRANSIT_STATUS_RE.search(text):
        names.add("transit_snapshot")
    if _TRANSIT_ACCESSIBILITY_RE.search(text):
        names.add("accessibility_status")
    if _TRANSIT_EVENT_RE.search(text):
        names.update(("event_lookup", "venue_crowd_window"))
    if _CURRENT_REPORTING_RE.search(text):
        names.add("web_search")
    return frozenset(names)


def parse_arrival_intent(message: str) -> ArrivalIntent:
    text = " ".join(str(message or "").replace("’", "'").split())
    text = re.sub(r"\bwhen's\b", "when is", text, flags=re.IGNORECASE)
    route_id = _arrival_route_id(text)
    has_arrival_noun = bool(_ARRIVAL_NOUN_RE.search(text))
    has_vehicle = bool(_ARRIVAL_VEHICLE_RE.search(text))
    has_timing = bool(_ARRIVAL_TIMING_RE.search(text))
    implicit = bool(_IMPLICIT_ARRIVAL_RE.search(text))
    route_availability = bool(
        route_id
        and re.search(
            r"\b(?:any|nearby|around)\b",
            text,
            re.IGNORECASE,
        )
    )

    # "When will I arrive?" is a destination ETA question, not a vehicle
    # prediction request. Arrival nouns, a route identifier, or an explicit
    # train/bus phrase are required before timing words can classify it.
    requested = (
        has_arrival_noun
        or implicit
        or route_availability
        or bool(route_id and has_timing)
    )
    requested = requested or bool(has_vehicle and has_timing and "arrive" in text.casefold())
    if not requested:
        return ArrivalIntent(requested=False)

    stop_query = None
    stop_match = re.search(
        r"\b(?:at|from)\s+(.+?)(?=\?|$)",
        text,
        re.IGNORECASE,
    )
    if stop_match:
        stop_query = stop_match.group(1).strip(" .?")

    direction_match = re.search(
        r"\b(?:uptown|downtown|northbound|southbound|"
        r"manhattan-bound|brooklyn-bound|queens-bound|bronx-bound)\b",
        text,
        re.IGNORECASE,
    )
    catchability = bool(re.search(r"\b(?:make|catch)\b", text, re.IGNORECASE))
    confidence = 0.98 if has_arrival_noun else 0.94 if route_id else 0.9
    return ArrivalIntent(
        requested=True,
        route_id=route_id,
        stop_query=stop_query,
        direction_query=direction_match.group(0) if direction_match else None,
        use_active_trip=stop_query is None,
        include_multiple_arrivals=True,
        catchability_requested=catchability,
        confidence=confidence,
    )


def _arrival_route_id(text: str) -> str | None:
    for match in _ROUTE_ID_RE.finditer(text):
        value = match.group(0)
        # A lowercase article is not the A train. Uppercase A remains a
        # valid explicit route identifier in rider shorthand such as "next A".
        if value == "a":
            continue
        normalized = value.upper()
        if re.fullmatch(r"(?:M|B|Q|S|X)\d{1,3}S", normalized):
            normalized = normalized[:-1]
        return normalized
    return None


def _requested_route_ids(text: str) -> tuple[str, ...]:
    route_ids: list[str] = []
    for match in _REQUESTED_ROUTE_RE.finditer(text):
        raw_route_id = match.group("after_action") or match.group("before_noun") or ""
        # Lowercase "a route/train" is an article, not a request for the A.
        if raw_route_id == "a":
            continue
        route_id = raw_route_id.upper()
        if route_id not in route_ids:
            route_ids.append(route_id)
    return tuple(route_ids)


def _has_explicit_route_request(text: str) -> bool:
    """Distinguish "need the Q" from a status mention like "the Q train"."""

    return any(
        match.group("after_action")
        and match.group("after_action").casefold() != "a"
        for match in _REQUESTED_ROUTE_RE.finditer(text)
    )


def extract_excluded_route_ids(message: str) -> tuple[str, ...]:
    """Extract route exclusions from explicit negative phrases.

    Only unambiguous negatives (avoid/without/no/skip/not take, or "X
    train is out/closed") yield an excluded route id; "a" never does.
    """

    text = " ".join(str(message or "").split())
    route_ids: list[str] = []
    for match in _EXCLUDED_ROUTE_RE.finditer(text):
        raw_route_id = match.group("after_verb") or match.group("before_noun") or ""
        # "avoid a train" is about trains generally, not the A train.
        if raw_route_id == "a":
            continue
        route_id = raw_route_id.upper()
        if route_id not in route_ids:
            route_ids.append(route_id)
    return tuple(route_ids)


def extract_allowed_route_ids(message: str) -> tuple[str, ...]:
    """Extract route relaxations from imperative allowances or explicit
    changed-state statements ("allow the Q", "What if I allow the Q?",
    "the Q is fine now"). Meta/status questions, negated commands, and the
    article "a" never relax; punctuation alone never decides.
    """

    text = " ".join(str(message or "").split())
    if _NEGATED_ALLOWANCE_RE.search(text) or _META_ALLOWANCE_RE.search(text):
        return ()
    route_ids: list[str] = []
    for match in _ALLOWED_ROUTE_RE.finditer(text):
        raw_route_id = match.group("allow_route") or match.group("state_route") or ""
        # "allow a train" is about trains generally, not the A train.
        if raw_route_id == "a":
            continue
        route_id = raw_route_id.upper()
        if route_id not in route_ids:
            route_ids.append(route_id)
    return tuple(route_ids)


def normalize_route_id(value: object) -> str | None:
    """Return one bounded canonical route id, or None for junk input."""

    text = str(value or "").strip().upper()
    if not text or len(text) > MAX_ROUTE_ID_LENGTH:
        return None
    if not _ROUTE_ID_SHAPE_RE.fullmatch(text):
        return None
    return text


def normalize_route_ids(values: object) -> tuple[str, ...]:
    """Normalize, de-duplicate, and bound a collection of route ids.

    Only array-like collections are accepted; junk is treated as empty so
    a string is never split into characters. List/tuple keep caller order;
    set/frozenset are sorted for determinism. Iteration stops after
    MAX_NORMALIZED_ROUTE_IDS unique ids.
    """

    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    if isinstance(values, (set, frozenset)):
        values = sorted(values, key=lambda value: str(value or "").strip().upper())
    route_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        route_id = normalize_route_id(value)
        if route_id is not None and route_id not in seen:
            seen.add(route_id)
            route_ids.append(route_id)
            if len(route_ids) >= MAX_NORMALIZED_ROUTE_IDS:
                break
    return tuple(route_ids)


def is_new_trip_request(message: str) -> bool:
    text = str(message or "")
    if re.search(
        r"\b(?:retry|resume|again|that route|same trip|what\s+if|"
        r"for\s+(?:this|that)\s+trip)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return bool(
        _EXPLICIT_SAVED_ENDPOINT_RE.search(text)
        or _NEW_TRIP_RE.search(text)
        or _SLANG_ROUTE_REQUEST_RE.search(text)
    )


def is_what_if_request(message: str) -> bool:
    """Identify explicit hypothetical route comparisons without using a model."""

    return bool(
        re.search(
            r"\b(?:what\s+if|for\s+(?:this|that)\s+trip|as\s+an\s+alternative)\b",
            str(message or ""),
            re.IGNORECASE,
        )
    )


def deterministic_response(message: str) -> str | None:
    """Handle only unambiguous non-transit turns without model or tools."""

    text = " ".join(str(message or "").split()).strip()
    if _GREETING_RE.fullmatch(text):
        return "Hi — I can plan NYC subway and bus trips, check arrivals, and explain service changes."
    if _THANKS_RE.fullmatch(text):
        return "You’re welcome."
    if _HELP_RE.fullmatch(text):
        return "Tell me where you’re starting and going, or ask about a train or bus arrival."
    if _OFF_TOPIC_RE.fullmatch(text):
        return "SmartRoute is for NYC transit help. I can plan a subway or bus trip, compare routes, or check arrivals."
    return evaluate_simple_arithmetic(text)


def evaluate_simple_arithmetic(message: str) -> str | None:
    """Evaluate a deliberately tiny arithmetic grammar, never arbitrary code."""

    candidate = str(message or "").strip().rstrip("?.")
    candidate = re.sub(r"^(?:what(?:'s| is)|calculate)\s+", "", candidate, flags=re.IGNORECASE)
    if not candidate or len(candidate) > 80 or not re.fullmatch(r"[\d\s+\-*/().%]+", candidate):
        return None
    try:
        tree = ast.parse(candidate, mode="eval")
        value = _eval_math_node(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, float):
        return f"{value:.10g}."
    return f"{value}."


def _eval_math_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if abs(node.value) > 1_000_000_000:
            raise ValueError("number too large")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SUPPORTED_UNARY:
        return _SUPPORTED_UNARY[type(node.op)](_eval_math_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _SUPPORTED_BINOPS:
        left = _eval_math_node(node.left)
        right = _eval_math_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 8:
            raise ValueError("exponent too large")
        result = _SUPPORTED_BINOPS[type(node.op)](left, right)
        if abs(result) > 1_000_000_000_000:
            raise ValueError("result too large")
        return result
    raise ValueError("unsupported expression")

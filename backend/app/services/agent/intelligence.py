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
    r"(?:M\d{1,3}|B\d{1,3}|Q\d{1,3}|S\d{1,3}|X\d{1,3}|"
    r"[1234567ABCDEFGJLMNQRSWZ])"
)
_REQUESTED_ROUTE_RE = re.compile(
    rf"\b(?:take|use|using|via|ride|prefer|want)\s+(?:the\s+)?"
    rf"(?P<after_action>{_ROUTE_ID_TOKEN})"
    rf"(?:\s+(?:train|subway|bus|line|route))?\b|"
    rf"\b(?P<before_noun>{_ROUTE_ID_TOKEN})\s+"
    rf"(?:train|subway|bus|line|route)\b",
    re.IGNORECASE,
)
_NEW_TRIP_RE = re.compile(
    r"\b(?:get|take|route|directions?|head(?:ing)?|travel|trip)\b.{0,32}\b(?:to|from)\b|"
    r"\b(?:go|going)\s+to\b",
    re.IGNORECASE,
)
_DISCOVERY_RE = re.compile(
    r"\b(?:find|recommend|suggest(?:ion)?s?|best|good|great|where\s+should\s+i|"
    r"in\s+the\s+mood\s+for)\b.{0,72}\b(?:pizza|pancakes?|breakfast|brunch|"
    r"restaurant|food|eat|dinner|lunch|bar|cafe|coffee|bakery|dessert|"
    r"museum|park|show|venue|place|somewhere|go)\b",
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
            tools.append("poi_search")
        if self.routes:
            tools.append("plan_trip")
        if self.arrivals:
            tools.append("lookup_arrivals")
        # Event collection for routes is owned by plan_trip so evidence is
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
    avoid_crowds = bool(_CROWD_RE.search(text))
    arrival = parse_arrival_intent(text)
    if arrival.requested:
        return ParsedIntent(
            "arrival_lookup",
            avoid_crowds,
            arrival=arrival,
        )
    if evaluate_simple_arithmetic(text) is not None:
        return ParsedIntent("simple_general", avoid_crowds)
    if _GREETING_RE.fullmatch(text) or _THANKS_RE.fullmatch(text) or _HELP_RE.fullmatch(text):
        return ParsedIntent("simple_general", avoid_crowds)
    if _OFF_TOPIC_RE.fullmatch(text):
        return ParsedIntent("unsupported", avoid_crowds)
    if _DISCOVERY_RE.search(text):
        return ParsedIntent("destination_discovery", avoid_crowds)
    if _NEW_TRIP_RE.search(text):
        return ParsedIntent(
            "route_planning",
            avoid_crowds,
            requested_route_ids=_requested_route_ids(text),
        )
    if _AREA_CONDITIONS_RE.search(text):
        return ParsedIntent("area_conditions", avoid_crowds)
    return ParsedIntent("transit_question", avoid_crowds)


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


def is_new_trip_request(message: str) -> bool:
    text = str(message or "")
    if re.search(r"\b(?:retry|resume|again|that route|same trip)\b", text, re.IGNORECASE):
        return False
    return bool(_NEW_TRIP_RE.search(text))


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

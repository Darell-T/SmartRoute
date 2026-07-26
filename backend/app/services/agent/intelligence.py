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
    "simple_general",
    "transit_question",
    "unsupported",
]

_CROWD_RE = re.compile(
    r"\b(?:avoid|less|fewer)\s+(?:the\s+)?(?:crowd(?:s|ed)?|busy\s+stations?|"
    r"event\s+traffic|concert\s+crowds?|game\s+traffic|theater\s+crowds?)\b",
    re.IGNORECASE,
)
_ARRIVAL_RE = re.compile(
    r"\b(?:next|arriv(?:e|al|ing)|coming|how\s+long\s+until|will\s+i\s+make)\b",
    re.IGNORECASE,
)
_ROUTE_ID_RE = re.compile(
    r"\b(?:M\d{1,3}|B\d{1,3}|Q\d{1,3}|S\d{1,3}|X\d{1,3}|"
    r"[1234567ABCDEFGJLMNQRSWZ])\b",
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
    r"\b(?:get|take|route|directions?|heading|travel|trip)\b.{0,32}\b(?:to|from)\b|"
    r"\b(?:go|going)\s+to\b",
    re.IGNORECASE,
)
_DISCOVERY_RE = re.compile(
    r"\b(?:find|recommend|best|good)\b.{0,40}\b(?:pizza|restaurant|food|eat|bar|cafe|place)\b",
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
    arrival_route_id: str | None = None
    arrival_stop_query: str | None = None
    requested_route_ids: tuple[str, ...] = ()

    @property
    def required_evidence(self) -> RequiredEvidence:
        if self.intent == "arrival_lookup":
            return RequiredEvidence(arrivals=True)
        if self.intent == "destination_discovery":
            return RequiredEvidence(places=True, events=self.avoid_crowds)
        if self.intent == "route_planning":
            return RequiredEvidence(routes=True, service_alerts=True, events=self.avoid_crowds)
        return RequiredEvidence()


def parse_intent(message: str) -> ParsedIntent:
    text = " ".join(str(message or "").split())
    route_match = _ROUTE_ID_RE.search(text)
    avoid_crowds = bool(_CROWD_RE.search(text))
    if _ARRIVAL_RE.search(text) and (route_match or "my train" in text.casefold()):
        stop_query = None
        at_match = re.search(r"\b(?:at|from)\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
        if at_match:
            stop_query = at_match.group(1).strip(" .?")
        return ParsedIntent(
            "arrival_lookup",
            avoid_crowds,
            route_match.group(0).upper() if route_match else None,
            stop_query,
        )
    if evaluate_simple_arithmetic(text) is not None:
        return ParsedIntent("simple_general", avoid_crowds)
    if _DISCOVERY_RE.search(text):
        return ParsedIntent("destination_discovery", avoid_crowds)
    if _NEW_TRIP_RE.search(text):
        return ParsedIntent(
            "route_planning",
            avoid_crowds,
            requested_route_ids=_requested_route_ids(text),
        )
    return ParsedIntent("transit_question", avoid_crowds)


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

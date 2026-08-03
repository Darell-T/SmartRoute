"""Provider-facing strict tool schema compatibility."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.agent import intelligence, loop, policy
from app.services.agent.strict_tool_schema import (
    assert_strict_tool_schemas_compatible,
    iter_unsupported_strict_keyword_paths,
)
from app.services.agent.tools import TOOL_REGISTRY, TOOLS, plan_trip


class StrictToolSchemaTests(unittest.TestCase):
    def test_registry_tools_have_no_unsupported_strict_keywords(self):
        assert_strict_tool_schemas_compatible(TOOLS)
        for name, spec in TOOL_REGISTRY.items():
            self.assertTrue(spec.schema.get("strict"), name)
            self.assertIn("input_schema", spec.schema)
            self.assertEqual(spec.schema["name"], name)

    def test_auto_and_quick_intent_profiles_are_strict_compatible(self):
        intents = (
            intelligence.ParsedIntent(intent="route_planning", avoid_crowds=False),
            intelligence.ParsedIntent(intent="destination_discovery", avoid_crowds=False),
            intelligence.ParsedIntent(intent="arrival_lookup", avoid_crowds=False),
            intelligence.ParsedIntent(intent="area_conditions", avoid_crowds=False),
            intelligence.ParsedIntent(intent="transit_question", avoid_crowds=False),
        )
        for parsed in intents:
            for mode in ("auto", "quick"):
                with self.subTest(intent=parsed.intent, mode=mode):
                    tools = loop._tools_for_intent(parsed, policy.policy_for_mode(mode))
                    custom = [tool for tool in tools if tool.get("strict")]
                    assert_strict_tool_schemas_compatible(custom)
                    names = {tool.get("name") for tool in custom}
                    if parsed.intent == "route_planning":
                        self.assertIn("plan_trip", names)
                        self.assertIn("accessibility_status", names)
                    if parsed.intent == "arrival_lookup":
                        self.assertIn("lookup_arrivals", names)
                    if parsed.intent == "destination_discovery":
                        self.assertIn("poi_search", names)
                        self.assertIn("plan_trip", names)
                    if parsed.intent == "area_conditions":
                        self.assertEqual(names, {"check_area_conditions"})
                        self.assertEqual(
                            {tool.get("name") for tool in tools},
                            {"check_area_conditions"},
                        )

    def test_plan_trip_required_fields_remain(self):
        schema = plan_trip.PLAN_TRIP_SCHEMA
        self.assertEqual(schema["name"], "plan_trip")
        self.assertTrue(schema["strict"])
        props = schema["input_schema"]["properties"]
        self.assertIn("origin", props)
        self.assertIn("destination", props)
        self.assertIn("waypoints", props)
        self.assertEqual(schema["input_schema"]["required"], ["origin", "destination"])
        self.assertNotIn("maxItems", props["waypoints"])
        self.assertNotIn("maxLength", props["waypoints"].get("items") or {})

    def test_recursive_scanner_flags_max_items(self):
        bad = {
            "type": "object",
            "properties": {
                "waypoints": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {"type": "string", "maxLength": 160},
                }
            },
        }
        paths = iter_unsupported_strict_keyword_paths(bad)
        self.assertIn("$.properties.waypoints.maxItems", paths)
        self.assertIn("$.properties.waypoints.items.maxLength", paths)
        with self.assertRaises(AssertionError):
            assert_strict_tool_schemas_compatible(
                [{"name": "plan_trip", "strict": True, "input_schema": bad}]
            )

    def test_default_auto_model_is_sonnet_four_five(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            self.assertEqual(
                policy.policy_for_mode("auto").model,
                "claude-sonnet-4-5-20250929",
            )


if __name__ == "__main__":
    unittest.main()

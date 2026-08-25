"""Provider-facing strict tool schema compatibility."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.agent import loop, public_surface
from app.services.agent.model import policy
from app.services.agent.tools import (
    assert_strict_tool_schemas_compatible,
    iter_unsupported_strict_keyword_paths,
)
from app.services.agent.tools import TOOL_REGISTRY, TOOLS
from app.services.agent.tools.route.present_route import PRESENT_ROUTE_SCHEMA
from app.services.agent.tools.route.prepare_route_options import PREPARE_ROUTE_OPTIONS_SCHEMA


class StrictToolSchemaTests(unittest.TestCase):
    def test_registry_tools_have_no_unsupported_strict_keywords(self):
        assert_strict_tool_schemas_compatible(TOOLS)
        for name, spec in TOOL_REGISTRY.items():
            self.assertTrue(spec.schema.get("strict"), name)
            self.assertIn("input_schema", spec.schema)
            self.assertEqual(spec.schema["name"], name)

    def test_destination_branch_schema_covers_route_dependent_choice(self):
        description = " ".join(
            PREPARE_ROUTE_OPTIONS_SCHEMA["input_schema"]["properties"][
                "destination_place_ids"
            ]["description"].casefold().split()
        )
        self.assertIn("route-dependent delegated destination choice", description)
        self.assertIn("least walking", description)
        self.assertIn("fewer transfers", description)
        self.assertIn("even when the rider does not explicitly ask to compare", description)
        self.assertIn("route-independent place-only criteria", description)
        self.assertNotIn(
            "use this when the rider asks smartroute to compare verified locations",
            description,
        )

    def test_auto_and_quick_intent_profiles_are_strict_compatible(self):
        expected = set(public_surface.INITIAL_TOOL_NAMES)
        expected_strict: set[str] = set()
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                tools = loop._tools_for_state(policy.policy_for_mode(mode))
                assert_strict_tool_schemas_compatible(tools)
                names = {tool.get("name") for tool in tools}
                strict_names = {
                    tool.get("name") for tool in tools if tool.get("strict")
                }
                self.assertEqual(names, expected)
                self.assertEqual(strict_names, expected_strict)
                self.assertNotIn("poi_search", names)
                self.assertNotIn("plan_trip", names)

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

    def test_default_auto_model_is_sonnet_five(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            self.assertEqual(
                policy.policy_for_mode("auto").model,
                "claude-sonnet-5",
            )

    def test_present_route_framing_contract_requires_supported_qualitative_reason(self):
        properties = PRESENT_ROUTE_SCHEMA["input_schema"]["properties"]
        lead_in = properties["lead_in"]["description"].casefold()
        reason_code = properties["reason_code"]["description"].casefold()
        self.assertIn("every successful route needs one", lead_in)
        self.assertIn("unqualified request", lead_in)
        self.assertIn("qualitative transfer, walking, disruption, crowd", lead_in)
        self.assertIn("no digits", lead_in)
        self.assertIn("name the supported factor directly", lead_in)
        self.assertIn("no comparative factor or explicit rider constraint", lead_in)
        self.assertIn("options were close", lead_in)
        self.assertIn("nothing had a clear edge", lead_in)
        self.assertIn("covers what the rider asked for", lead_in)
        self.assertIn("expose backend language", lead_in)
        self.assertIn("canonical itinerary supports it", lead_in)
        self.assertIn("hard validity alone does not prove route shape", lead_in)
        self.assertIn("every successful route presentation requires one", reason_code)
        self.assertIn(
            "fits, satisfies constraints, is best, is practical, or satisfies the trip",
            reason_code,
        )


if __name__ == "__main__":
    unittest.main()

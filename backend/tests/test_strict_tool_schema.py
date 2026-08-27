"""Provider-facing strict tool schema compatibility."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pytest
from app.services.agent import loop, public_surface
from app.services.agent.model import policy
from app.services.agent.tools import (
    TOOL_REGISTRY,
    TOOLS,
    assert_strict_tool_schemas_compatible,
    iter_unsupported_strict_keyword_paths,
)
from app.services.agent.tools.route.prepare_route_options import (
    PREPARE_ROUTE_OPTIONS_SCHEMA,
)
from app.services.agent.tools.route.present_route import PRESENT_ROUTE_SCHEMA


class StrictToolSchemaTests(unittest.TestCase):
    def test_registry_tools_have_no_unsupported_strict_keywords(self):
        assert_strict_tool_schemas_compatible(TOOLS)
        for name, spec in TOOL_REGISTRY.items():
            assert spec.schema.get("strict"), name
            assert "input_schema" in spec.schema
            assert spec.schema["name"] == name

    def test_destination_branch_schema_covers_route_dependent_choice(self):
        description = " ".join(
            PREPARE_ROUTE_OPTIONS_SCHEMA["input_schema"]["properties"][
                "destination_place_ids"
            ]["description"]
            .casefold()
            .split()
        )
        assert "route-dependent delegated destination choice" in description
        assert "least walking" in description
        assert "fewer transfers" in description
        assert "even when the rider does not explicitly ask to compare" in description
        assert "route-independent place-only criteria" in description
        assert (
            "use this when the rider asks smartroute to compare verified locations"
            not in description
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
                assert names == expected
                assert strict_names == expected_strict
                assert "poi_search" not in names
                assert "plan_trip" not in names

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
        assert "$.properties.waypoints.maxItems" in paths
        assert "$.properties.waypoints.items.maxLength" in paths
        with pytest.raises(AssertionError):
            assert_strict_tool_schemas_compatible(
                [{"name": "plan_trip", "strict": True, "input_schema": bad}]
            )

    def test_default_auto_model_is_sonnet_five(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            assert policy.policy_for_mode("auto").model == "claude-sonnet-5"

    def test_present_route_framing_contract_requires_supported_qualitative_reason(self):
        properties = PRESENT_ROUTE_SCHEMA["input_schema"]["properties"]
        lead_in = properties["lead_in"]["description"].casefold()
        reason_code = properties["reason_code"]["description"].casefold()
        assert "every successful route needs one" in lead_in
        assert "unqualified request" in lead_in
        assert "qualitative transfer, walking, disruption, crowd" in lead_in
        assert "no digits" in lead_in
        assert "name the supported factor directly" in lead_in
        assert "no comparative factor or explicit rider constraint" in lead_in
        assert "options were close" in lead_in
        assert "nothing had a clear edge" in lead_in
        assert "covers what the rider asked for" in lead_in
        assert "expose backend language" in lead_in
        assert "canonical itinerary supports it" in lead_in
        assert "hard validity alone does not prove route shape" in lead_in
        assert "every successful route presentation requires one" in reason_code
        assert (
            "fits, satisfies constraints, is best, is practical, or satisfies the trip"
            in reason_code
        )


if __name__ == "__main__":
    unittest.main()

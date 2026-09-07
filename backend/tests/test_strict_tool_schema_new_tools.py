"""Strict schema coverage for new agent tools."""

from __future__ import annotations

import unittest

from app.services.agent.tools import (
    COMBINED_TOOL_REGISTRY,
    TOOLS,
    assert_strict_tool_schemas_compatible,
)
from app.services.agent.tools.route.prepare_route_options import (
    PREPARE_ROUTE_OPTIONS_SCHEMA,
)
from app.services.agent.tools.route.present_route import PRESENT_ROUTE_SCHEMA
from app.services.agent.tools.transit.check_area_conditions import (
    AREA_CONDITIONS_SCHEMA,
)
from app.services.agent.tools.transit.present_transit import PRESENT_TRANSIT_SCHEMA


class NewToolStrictSchemaTests(unittest.TestCase):
    def test_registry_includes_new_tools_and_is_strict_compatible(self):
        names = set(COMBINED_TOOL_REGISTRY)
        for required in (
            "prepare_route_options",
            "present_route",
            "discover_places",
            "get_place_details",
        ):
            assert required in names
        assert "plan_trip" not in names
        assert_strict_tool_schemas_compatible(TOOLS)
        for name, spec in COMBINED_TOOL_REGISTRY.items():
            assert spec.schema.get("strict"), name
            assert spec.schema["name"] == name

    def test_present_route_contract_matches_the_actual_model_input(self):
        schema = PRESENT_ROUTE_SCHEMA["input_schema"]
        assert set(schema["properties"]) == {"candidate_id", "commit_scenario", "goal_key", "lead_in", "follow_up", "reason_code"}
        assert schema["required"] == ["candidate_id", "goal_key", "lead_in", "follow_up", "reason_code"]
        assert not schema["additionalProperties"]

    def test_present_transit_supports_natural_framing_around_canonical_facts(self):
        schema = PRESENT_TRANSIT_SCHEMA["input_schema"]
        assert set(schema["properties"]) == {"evidence_set_id", "goal_key", "lead_in", "follow_up"}
        assert schema["required"] == ["evidence_set_id", "goal_key", "lead_in", "follow_up"]
        assert not schema["additionalProperties"]

    def test_prepare_route_options_schema_accepts_excluded_route_ids(self):
        schema = PREPARE_ROUTE_OPTIONS_SCHEMA["input_schema"]
        assert "excluded_route_ids" in schema["properties"]
        assert schema["properties"]["excluded_route_ids"]["items"] == {"type": "string"}
        assert "required_route_ids" in schema["properties"]
        assert set(schema["properties"]) == set(schema["required"])
        assert not schema["additionalProperties"]

    def test_area_conditions_schema_directs_directions_to_canonical_workflow(self):
        description = AREA_CONDITIONS_SCHEMA["description"]
        assert "prepare_route_options" in description
        assert "present_route" in description
        assert "plan_trip" not in description


if __name__ == "__main__":
    unittest.main()

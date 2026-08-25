from __future__ import annotations


from tests.agent_loop_reliability_support import AgentLoopReliabilityTestCase


class AgentLoopToolSurfaceTests(AgentLoopReliabilityTestCase):
    async def test_route_planning_uses_the_stable_public_tool_profile(self):
        schemas = self.loop._tools_for_state()

        self.assertEqual(
            {schema["name"] for schema in schemas},
            set(self.loop.public_surface.INITIAL_TOOL_NAMES),
        )

    async def test_transit_operations_share_the_stable_public_surface(self):
        transit_names = {
            schema["name"]
            for schema in self.loop._tools_for_state()
        }
        expected = set(self.loop.public_surface.INITIAL_TOOL_NAMES)
        self.assertEqual(transit_names, expected)
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                names = {
                    schema["name"]
                    for schema in self.loop._tools_for_state(
                        self.loop.agent_policy.policy_for_mode(mode)
                    )
                }
                self.assertEqual(names, expected)
                self.assertNotIn("plan_trip", names)
                self.assertNotIn("poi_search", names)

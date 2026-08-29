from __future__ import annotations

import unittest

from app.services.agent.passenger_output import (
    MAX_ACTIVITY_LABEL_CHARS,
    MAX_PRESENTATION_FRAMING_CHARS,
    MAX_TERMINAL_MESSAGE_CHARS,
    pop_activity_label,
    validated_activity_label,
    validated_presentation_framing,
    validated_terminal_message,
)


class ActivityCopyTests(unittest.TestCase):
    def test_accepts_contextual_work_in_progress(self) -> None:
        label = "Reworking your trip without the L…"
        assert validated_activity_label(label) == label

    def test_simple_action_may_omit_copy(self) -> None:
        assert validated_activity_label(None) is None
        assert validated_activity_label("") is None

    def test_rejects_multiline_long_and_fluff_only_copy(self) -> None:
        assert validated_activity_label("Checking service\nnear you") is None
        assert validated_activity_label("x" * (MAX_ACTIVITY_LABEL_CHARS + 1)) is None
        assert validated_activity_label("Got it") is None

    def test_rejects_internal_names_and_opaque_ids(self) -> None:
        for label in (
            "Calling the route tool…",
            "Reading the backend schema…",
            "Looking up candidate_id cd_secret…",
            "Sending an API request…",
            "Asking Sonnet to choose…",
            "Checking Google results…",
            "Calling Anthropic…",
            "Researching with Grok…",
        ):
            with self.subTest(label=label):
                assert validated_activity_label(label) is None

    def test_rejects_timing_promises_and_unverified_results(self) -> None:
        for label in (
            "I'll have this ready in a moment",
            "Finding this in 5 seconds…",
            "Checking the 5-minute arrival…",
            "Found the best route…",
            "No delays on the Q",
            "We've found a route without the L",
            "The Q has no delays",
            "These restaurants are open",
        ):
            with self.subTest(label=label):
                assert validated_activity_label(label) is None

    def test_display_metadata_is_removed_from_execution_input(self) -> None:
        tool_input = {
            "goal_key": "route",
            "activity_label": "Comparing subway routes with less walking…",
        }
        assert pop_activity_label(tool_input) == "Comparing subway routes with less walking…"
        assert tool_input == {"goal_key": "route"}

    def test_presentation_framing_allows_brief_conversation(self) -> None:
        text = "A few good options stood out."
        assert validated_presentation_framing(text) == text
        assert validated_presentation_framing("") == ""

    def test_presentation_framing_rejects_internal_or_unbounded_copy(self) -> None:
        for text in (
            "Showing candidate_id cd_secret.",
            "Using place_id pl_secret.",
            "Resolved ChIJabcdefghijk.",
            "I accounted for avoid_crowds.",
            "x" * (MAX_PRESENTATION_FRAMING_CHARS + 1),
            "First line\nSecond line",
        ):
            with self.subTest(text=text[:40]):
                assert validated_presentation_framing(text) is None

    def test_terminal_message_preserves_paragraphs_and_rejects_internals(self) -> None:
        assert validated_terminal_message("First line.\n\nSecond   line.") == "First line.\n\nSecond line."
        assert validated_terminal_message("Applied avoid_crowds.") is None
        assert validated_terminal_message("x" * (MAX_TERMINAL_MESSAGE_CHARS + 1)) is None

    def test_terminal_message_normalizes_edges_and_rejects_non_text(self) -> None:
        assert validated_terminal_message("\n  Ready to go.  \n\n") == "Ready to go."
        assert validated_terminal_message(None) is None

    def test_ordinary_terminal_message_rejects_unowned_continuation(self) -> None:
        assert validated_terminal_message("I can check that when you are ready.", outcome="answer") is None


if __name__ == "__main__":
    unittest.main()

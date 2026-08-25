"""Transport contracts for typed passenger actions."""

from app.services.agent.events import TransitStatusActionEvent, sse_format


def test_transit_status_action_event_is_narrow_and_serializable() -> None:
    event = TransitStatusActionEvent(turn_id="turn-1")

    assert event.to_data() == {"turn_id": "turn-1", "action": "view_alerts"}
    assert sse_format(event) == (
        'event: transit_status_action\n'
        'data: {"turn_id":"turn-1","action":"view_alerts"}\n\n'
    )

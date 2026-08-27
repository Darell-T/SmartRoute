"""Transport contracts for typed passenger actions and trusted sources."""

import pytest
from app.services.agent.events import (
    SourcesEvent,
    TransitStatusActionEvent,
    sse_format,
)


def test_transit_status_action_event_is_narrow_and_serializable() -> None:
    event = TransitStatusActionEvent(turn_id="turn-1")

    assert event.to_data() == {"turn_id": "turn-1", "action": "view_alerts"}
    assert sse_format(event) == (
        'event: transit_status_action\n'
        'data: {"turn_id":"turn-1","action":"view_alerts"}\n\n'
    )


def test_sources_event_accepts_normalized_https_urls() -> None:
    event = SourcesEvent(
        turn_id="turn-1",
        sources=(
            {
                "title": "Damn Lines: L'industrie Pizzeria",
                "url": "https://damnlines.com/camera/lindustrie-pizzeria",
            },
            {
                "title": "Damn Lines: L'industrie Pizzeria",
                "url": "https://damnlines.com/camera/lindustrie-pizzeria",
            },
        ),
    )

    assert event.to_data() == {
        "sources": [
            {
                "title": "Damn Lines: L'industrie Pizzeria",
                "url": "https://damnlines.com/camera/lindustrie-pizzeria",
            }
        ]
    }


def test_sources_event_rejects_untrusted_urls() -> None:
    untrusted = (
        "http://damnlines.com/camera/lindustrie-pizzeria",
        "https://damnlines.com:444/camera/lindustrie-pizzeria",
        "https://user:pass@damnlines.com/camera/lindustrie-pizzeria",
        "not-a-url",
    )
    for url in untrusted:
        with pytest.raises(ValueError, match="source is not trusted"):
            SourcesEvent(
                turn_id="turn-1",
                sources=({"title": "Damn Lines", "url": url},),
            )

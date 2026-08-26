"""Transport contracts for typed passenger actions and trusted sources."""

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


def test_sources_event_accepts_only_configured_damn_lines_urls() -> None:
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
        "https://example.com/camera/lindustrie-pizzeria",
        "https://damnlines.com/evil",
        "https://user:pass@damnlines.com/camera/lindustrie-pizzeria",
    )
    for url in untrusted:
        try:
            SourcesEvent(
                turn_id="turn-1",
                sources=({"title": "Damn Lines", "url": url},),
            )
        except ValueError as exc:
            assert "source is not trusted" in str(exc)
        else:
            raise AssertionError(url)

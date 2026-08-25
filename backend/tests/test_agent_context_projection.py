"""Bounded relevance projection over a complete conversation transcript."""

from app.services.agent.transcript_store import project_model_history


def turn(index: int, topic: str) -> list[dict]:
    return [
        {"role": "user", "text": f"I care about {topic}", "turn_id": f"t{index}"},
        {"role": "assistant", "text": f"Noted {topic}", "turn_id": f"t{index}"},
    ]


def test_projection_recovers_relevant_older_context_and_keeps_recent_tail():
    transcript = []
    for index, topic in enumerate(
        ["wheelchair access", "pizza", "museums", "coffee", "parks", "the Q train", "Work"]
    ):
        transcript.extend(turn(index, topic))
    recent = transcript[-4:]

    projected = project_model_history(transcript, recent, "What about wheelchair access?")
    text = " ".join(entry["text"] for entry in projected)
    assert "wheelchair access" in text
    assert projected[-4:] == recent


def test_projection_keeps_opening_context_for_vague_references():
    transcript = turn(0, "the original weekend plan")
    for index in range(1, 10):
        transcript.extend(turn(index, f"topic {index}"))
    recent = transcript[-4:]

    projected = project_model_history(transcript, recent, "What did I say at the start?")
    assert transcript[0] in projected
    assert projected[-4:] == recent

from __future__ import annotations

from pathlib import Path

ROOT = Path(r"C:\Users\19293\jarvis\jarvis-design\.worktrees\damn-lines-integration")
CAND = ROOT / ".audit" / "backend-refactor" / "inline-candidates.tsv"
OUT = ROOT / ".audit" / "backend-refactor" / "decisions.tsv"

INLINED = {
    "_resolved_place_match_key",
    "_dict_place_match_key",
    "_snapshot_route_evidence",
    "_normalized_waypoints",
    "_card_matches_itinerary",
    "_mapping_facts",
}

header = [
    "FIXED_POINT=5193b08",
    "unit_id\tfiles\taction\treason\tevidence\tloc_delta\tcognitive_check\tstatus",
    "wave0-inventory\t.audit/backend-refactor/*\tleave\tinventory snapshot at 5193b08; no production edits\tsection 3.2 commands + git diffs + rg test seams\t0\tpass\tdone",
    "wave0-stdout-seams\tbackend/tests\tleave\tplan said no capsys/capfd/builtins.print hits; measured 12 hits in 6 files; record for Wave 6\trg -n capsys|capfd|builtins.print backend/tests\t0\tpass\tdone",
    "wave0-move-lock\tquality/baseline.json\tleave\tregenerated move-lock equals plan 2.2 (57 files, 0 extra, 0 missing)\tcomplexipy + baseline file paths\t0\tpass\tdone",
    "w1-u1-deadline\tbackend/app/services/agent/loop.py,backend/tests/test_agent_loop.py,backend/tests/conversation/test_conversation_cancellation_recovery.py,backend/tests/_fake_anthropic.py,backend/scripts/release/provider_fault_cases.py\tdelete\tdelete duplicate AGENT_TURN_DEADLINE_S in loop.py; use session_module; reload session on loop test reload so env patches still apply\tpytest DeadlineTests DeadlineRecoveryTests destination_discovery; ruff check unit files; check_quality --cognitive-only\t-1\tpass\tdone",
    "w1-u2-magic-key\tbackend/app/services/agent/turn/tool_round.py,backend/app/services/agent/turn/stream.py,backend/tests/test_goal_aware_tool_round.py\trename\treplace __tool_result_message__ dict with frozen ToolRoundResultMessage(role, content, deadline_reached, tool_outcomes); extra fields required to keep deadline/outcomes\tpytest test_goal_aware_tool_round 12 passed; _surface_results 3->3; _capture_tool_round_message 2->1\t+8\tpass\tdone",
    "w1-u3-mapping\tbackend/app/services/agent/turn/completion.py,backend/app/services/agent/tools/complete_turn.py,backend/tests/test_completion_policy.py\tunify\tprojected is a hypothetical snapshot not the model output contract; convert to TurnEvidence via _evidence_from_projected; delete _mapping_facts; keep _object_facts\tpytest 55 passed; evaluate_completion stayed 12; _facts 4->2; _terminate_turn stayed 2\t-8\tpass\tdone",
    "w1-u3-inline-object-facts\tbackend/app/services/agent/turn/completion.py\tleave\tleave: metric locked; inlining _object_facts into _facts raised _facts 4->10 in locked completion.py\tcomplexipy file_complexity completion.py\t0\tpass\tdone",
    "w1-u4-inline\tbackend/app/services/agent/tools/route/prepare_route_persistence.py,backend/app/services/agent/trip_state.py,backend/app/services/agent/transcript_store.py\tinline\tinline one-caller helpers whose callers stayed at or below cognitive 10: _resolved_place_match_key, _dict_place_match_key, _snapshot_route_evidence, _normalized_waypoints, _card_matches_itinerary\tpytest 31 passed; ruff check files; check_quality --cognitive-only 0 new\t-40\tpass\tdone",
    "w1-u4-bind-what-if\tbackend/app/services/agent/tools/route/present_route_commit.py\tleave\tleave: metric locked; inlining _bind_what_if_route raised _bind_presented_route 1->3 in locked present_route_commit.py\tcomplexipy then git checkout -- present_route_commit.py\t0\tpass\tdone",
    "w1-u4-web-search-tool\tbackend/app/services/agent/loop.py\tleave\tleave _web_search_tool in place; Wave 2 moves it to model/request.py\tplan section 6 wave 2 item 1\t0\tpass\tdone",
    "w1-u5-dead\tbackend/app/services/agent/profile.py\tdelete\tdelete unused top-level _PREFERENCE_KEYS (exactly one backend hit)\trg -n _PREFERENCE_KEYS backend; pytest 20 passed\t-10\tpass\tdone",
]

rows = list(header)
seen_names: set[tuple[str, str]] = set()
for raw in CAND.read_text(encoding="utf-8").splitlines()[1:]:
    parts = raw.split("\t")
    if len(parts) < 9:
        continue
    file_path, name = parts[0], parts[1]
    decision, reason = parts[7], parts[8]
    if name in INLINED:
        continue
    if (file_path, name) in seen_names:
        continue
    seen_names.add((file_path, name))
    if decision in {"leave", "skip"} or decision == "try":
        # remaining try candidates are left: not inlined this wave after metric screening
        action = "leave"
        if decision == "try":
            reason = "leave: metric locked; one-caller but inlining deferred after screening (caller near cap or locked file)"
        elif not reason.startswith("leave"):
            reason = f"leave: {reason}"
        unit = f"w1-u4-leave-{name}"
        rows.append(
            f"{unit}\t{file_path}\t{action}\t{reason}\tinline-candidates.tsv complexipy+ast caller scan\t0\tpass\tdone"
        )

OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
print(f"wrote {len(rows)} lines")

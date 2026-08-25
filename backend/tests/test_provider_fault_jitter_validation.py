"""Focused coverage for fixed-seed offline provider-fault release evidence."""

from __future__ import annotations

from scripts.release import provider_fault_validation as validation


def test_fixed_seed_provider_fault_validation_uses_only_offline_production_seams() -> None:
    status, reason, evidence = validation.run_provider_fault_jitter_validation()

    assert status == "PASSED"
    assert reason == "fixed-seed offline provider fault validation passed"
    assert evidence == {
        "seeds": "37,73,109",
        "named_cases": "malformed_payload,optional_provider_failure,invalid_request,invalid_credentials,rate_limited,model_unavailable,deadline_stall,disconnect,stream_jitter,agent_turn_terminal",
        "named_case_count": 10,
        "seeded_case_runs": 21,
        "deadline_ms": 20,
        "deadline_wall_bound_ms": 350,
        "jitter_wall_bound_ms": 250,
        "network": "disabled_by_replay_and_fake_provider_seams",
    }


def test_provider_fault_validation_failure_never_reports_pass(monkeypatch) -> None:
    async def failed_validation():
        raise RuntimeError("simulated assertion failure")

    monkeypatch.setattr(validation, "_validate", failed_validation)

    status, reason, evidence = validation.run_provider_fault_jitter_validation()

    assert status == "FAILED"
    assert reason == "offline provider fault validation failed: RuntimeError"
    assert evidence == {}

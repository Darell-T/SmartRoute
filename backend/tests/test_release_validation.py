"""Focused unit tests for the offline-safe release validation command."""

from __future__ import annotations

import asyncio
import json

from scripts import release_validation
from scripts import release_validation_transport as transport


def run_command(*arguments: str) -> dict[str, object]:
    return release_validation.run(release_validation.arguments(list(arguments)))


def checks_by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    checks = report["checks"]
    assert isinstance(checks, list)
    assert all(isinstance(item, dict) for item in checks)
    return {str(item["name"]): item for item in checks}


def browser_evidence(candidate_sha: str = "a1b2c3d4") -> dict[str, object]:
    required = ["chat", "quick_mode", "map_handoff", "accessibility", "shell", "zoom"]
    return {
        "schema_version": 1,
        "candidate": {"commit_sha": candidate_sha},
        "status": "PASSED",
        "runner": "playwright",
        "required_cases": required,
        "projects": {
            "desktop": {"passed_required_cases": required, "expected_skipped_cases": []},
            "mobile": {"passed_required_cases": required[:-1], "expected_skipped_cases": ["zoom"]},
        },
        "visual_comparison": {
            "certified": False,
            "scope": "platform_local_not_certified_in_linux_ci",
        },
    }


def test_offline_self_test_is_deterministic_and_uses_report_schema() -> None:
    report = run_command("--commit-sha", "a1b2c3d4", "--self-test")

    assert report["schema_version"] == 1
    assert report["candidate"] == {"commit_sha": "a1b2c3d4"}
    assert report["status"] == release_validation.STATUS_BLOCKED
    assert set(report) == {"schema_version", "candidate", "status", "budget", "checks"}
    checks = checks_by_name(report)
    assert checks["configuration_self_test"]["status"] == release_validation.STATUS_PASSED
    assert checks["dependency_advisories"]["status"] == release_validation.STATUS_NOT_APPLICABLE
    assert checks["migration_restore"]["status"] == release_validation.STATUS_NOT_APPLICABLE
    assert checks["browser_accessibility"]["status"] == release_validation.STATUS_BLOCKED
    assert checks["provider_fault_jitter"]["status"] == release_validation.STATUS_PASSED
    assert checks["provider_fault_jitter"]["evidence"] == {
        "seeds": "37,73,109",
        "named_cases": "malformed_payload,optional_provider_failure,invalid_request,invalid_credentials,rate_limited,model_unavailable,deadline_stall,disconnect,stream_jitter,agent_turn_terminal",
        "named_case_count": "10",
        "seeded_case_runs": "21",
        "deadline_ms": "20",
        "deadline_wall_bound_ms": "350",
        "jitter_wall_bound_ms": "250",
        "network": "disabled_by_replay_and_fake_provider_seams",
    }


def test_invalid_budget_blocks_before_any_network_work(monkeypatch) -> None:
    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("budget failures must precede network work")

    monkeypatch.setattr(transport, "http_check", unexpected_network)

    report = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "https://staging.example.test?token=should-not-leak",
        "--max-requests",
        "1",
    )

    assert report["status"] == release_validation.STATUS_FAILED
    configuration = checks_by_name(report)["configuration"]
    assert configuration["status"] == release_validation.STATUS_FAILED
    assert "exceeds max_requests" in configuration["reason"]
    assert "should-not-leak" not in json.dumps(report)


def test_provider_fault_check_fails_release_when_the_fixed_harness_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        release_validation,
        "run_provider_fault_jitter_validation",
        lambda: (release_validation.STATUS_FAILED, "offline fault failed", {}),
    )

    report = run_command("--commit-sha", "a1b2c3d4", "--self-test")

    assert report["status"] == release_validation.STATUS_FAILED
    assert checks_by_name(report)["provider_fault_jitter"] == {
        "name": "provider_fault_jitter",
        "status": release_validation.STATUS_FAILED,
        "reason": "offline fault failed",
        "evidence": {},
    }


def test_valid_browser_evidence_passes_the_offline_browser_gate(tmp_path) -> None:
    evidence_path = tmp_path / "browser-evidence.json"
    evidence_path.write_text(json.dumps(browser_evidence()), encoding="utf-8")

    report = run_command(
        "--commit-sha", "a1b2c3d4", "--self-test", "--browser-evidence", str(evidence_path)
    )

    check = checks_by_name(report)["browser_accessibility"]
    assert check["status"] == release_validation.STATUS_PASSED
    assert check["evidence"] == {
        "runner": "playwright",
        "desktop_required_cases": "6",
        "mobile_required_cases": "5",
        "mobile_expected_skips": "1",
        "visual_comparison": "platform_local_not_certified_in_linux_ci",
    }


def test_browser_evidence_accepts_case_insensitive_candidate_sha(tmp_path) -> None:
    evidence_path = tmp_path / "browser-evidence.json"
    evidence_path.write_text(json.dumps(browser_evidence("A1B2C3D4")), encoding="utf-8")

    report = run_command(
        "--commit-sha", "a1b2c3d4", "--self-test", "--browser-evidence", str(evidence_path)
    )

    assert checks_by_name(report)["browser_accessibility"]["status"] == release_validation.STATUS_PASSED


def test_browser_evidence_rejects_status_only_and_wrong_candidate(tmp_path) -> None:
    forged = tmp_path / "forged.json"
    wrong_candidate = tmp_path / "wrong-candidate.json"
    forged.write_text(json.dumps({"status": "PASSED"}), encoding="utf-8")
    wrong_candidate.write_text(json.dumps(browser_evidence("deadbeef")), encoding="utf-8")

    for evidence_path in (forged, wrong_candidate):
        report = run_command(
            "--commit-sha", "a1b2c3d4", "--self-test", "--browser-evidence", str(evidence_path)
        )
        assert report["status"] == release_validation.STATUS_FAILED
        assert checks_by_name(report)["browser_accessibility"]["status"] == release_validation.STATUS_FAILED


def test_redaction_removes_header_and_query_secrets() -> None:
    redacted = release_validation.redact(
        "https://stage.example.test/path?token=abc&safe=yes "
        "Authorization: Bearer bearer-secret X-App-Key: app-secret"
    )

    assert "abc" not in redacted
    assert "bearer-secret" not in redacted
    assert "app-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_staging_missing_scanner_and_deployment_evidence_is_blocked_without_network(monkeypatch) -> None:
    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("missing evidence must block staging network checks")

    monkeypatch.setattr(transport, "http_check", unexpected_network)
    report = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "https://staging.example.test",
    )

    checks = checks_by_name(report)
    assert report["status"] == release_validation.STATUS_BLOCKED
    assert checks["dependency_advisories"]["status"] == release_validation.STATUS_BLOCKED
    assert checks["deployment_evidence"]["status"] == release_validation.STATUS_BLOCKED
    assert checks["rollback_evidence"]["status"] == release_validation.STATUS_BLOCKED
    assert checks["chat_smoke"]["status"] == release_validation.STATUS_NOT_APPLICABLE


def test_deployment_evidence_requires_matching_sha_and_instance_ids(tmp_path) -> None:
    evidence = tmp_path / "deployment.json"
    evidence.write_text(json.dumps({"commit_sha": "deadbeef", "instance_ids": []}), encoding="utf-8")

    report = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "https://staging.example.test",
        "--deployment-evidence",
        str(evidence),
    )

    deployment = checks_by_name(report)["deployment_evidence"]
    assert deployment["status"] == release_validation.STATUS_FAILED
    assert "does not match candidate" in deployment["reason"]


def test_staging_evidence_runs_costed_model_chat_without_leaking_header(monkeypatch, tmp_path) -> None:
    deployment = tmp_path / "deployment.json"
    rollback = tmp_path / "rollback.json"
    deployment.write_text(json.dumps({"commit_sha": "a1b2c3d4", "instance_ids": ["staging-a"]}), encoding="utf-8")
    rollback.write_text(
        json.dumps({"previous_commit_sha": "beadfeed", "restored_commit_sha": "beadfeed", "result": "passed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_validation,
        "run_advisory_commands",
        lambda *_args: transport.ExternalCheckResult("PASSED", "scanner passed", {}),
    )
    monkeypatch.setattr(
        transport,
        "http_check",
        lambda *_args, **_kwargs: transport.TransportResult(True, 200, "response received"),
    )
    monkeypatch.setattr(
        transport,
        "chat_sse_check",
        lambda *_args, **_kwargs: transport.TransportResult(
            True,
            200,
            "successful SSE terminal event received",
        ),
    )

    report = run_command(
        "--commit-sha", "a1b2c3d4", "--staging", "--staging-url", "https://staging.example.test",
        "--advisory-command", "scanner", "--deployment-evidence", str(deployment),
        "--rollback-evidence", str(rollback), "--model-chat-smoke",
        "--chat-header", "X-App-Key: never-print-this", "--max-estimated-cost-usd", "0.01",
        "--estimated-cost-per-request-usd", "0.001",
    )

    checks = checks_by_name(report)
    assert report["status"] == release_validation.STATUS_BLOCKED
    assert all(
        checks[name]["status"] == release_validation.STATUS_PASSED
        for name in (
            "liveness",
            "readiness",
            "chat_smoke",
            "load_readiness_sample",
            "spike_readiness_sample",
            "soak_readiness_sample",
        )
    )
    assert "never-print-this" not in json.dumps(report)


def parse_sse(chunks: list[bytes], max_bytes: int = 128) -> transport.TransportResult:
    parser = transport.SseTerminalParser(max_bytes)
    for chunk in chunks:
        result = parser.feed(chunk)
        if result is not None:
            return result
    return parser.finish()


def test_chat_sse_requires_successful_bounded_terminal_event() -> None:
    error_result = parse_sse([b"event: error\n", b'data: {"code":"upstream_error"}\n', b"\n"])
    done_result = parse_sse([b"event: done\n", b'data: {"stop_reason":"end_turn"}\n', b"\n"])
    clarify_result = parse_sse([b"event: done\n", b'data: {"stop_reason":"clarification_required"}\n', b"\n"])
    missing_result = parse_sse([b"event: token\n", b'data: {"text":"hello"}\n', b"\n"])
    invalid_result = parse_sse([b"event: done\n", b"data: \xff\n", b"\n"])
    oversized_result = parse_sse([b"event: done\n", b'data: {"stop_reason":"end_turn"}\n', b"\n"], 8)

    assert not error_result.passed and "error event" in error_result.reason
    assert done_result.passed and done_result.reason == "successful SSE terminal event received"
    assert clarify_result.passed
    assert not missing_result.passed and "without a terminal" in missing_result.reason
    assert not invalid_result.passed and "invalid UTF-8" in invalid_result.reason
    assert not oversized_result.passed and "byte limit" in oversized_result.reason


def test_chat_sse_total_deadline_cancels_stalled_stream_without_sleep(monkeypatch) -> None:
    async def stalled_stream(*_args, **_kwargs):
        await asyncio.Future()

    monkeypatch.setattr(transport, "_read_chat_sse", stalled_stream)
    result = transport.chat_sse_check("https://stage.example.test/chat", 0.0, {}, b"{}", 128)

    assert not result.passed
    assert result.reason == "SSE stream exceeded total time limit"


def test_http_200_does_not_override_sse_error(monkeypatch) -> None:
    async def stream_result(_url, _timeout, _headers, body, _max_bytes):
        if body == b"error":
            return transport.TransportResult(False, 200, "SSE stream emitted an error event")
        return transport.TransportResult(True, 200, "successful SSE terminal event received")

    monkeypatch.setattr(transport, "_read_chat_sse", stream_result)
    error_result = transport.chat_sse_check("https://stage.example.test/chat", 1.0, {}, b"error", 128)
    done_result = transport.chat_sse_check("https://stage.example.test/chat", 1.0, {}, b"done", 128)

    assert error_result.status_code == 200 and not error_result.passed
    assert done_result.status_code == 200 and done_result.passed


def test_missing_scanner_executable_is_actionable_and_nonzero(monkeypatch, tmp_path, capsys) -> None:
    deployment = tmp_path / "deployment.json"
    rollback = tmp_path / "rollback.json"
    deployment.write_text(json.dumps({"commit_sha": "a1b2c3d4", "instance_ids": ["staging-a"]}), encoding="utf-8")
    rollback.write_text(
        json.dumps({"previous_commit_sha": "beadfeed", "restored_commit_sha": "beadfeed", "result": "passed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        transport,
        "http_check",
        lambda *_args, **_kwargs: transport.TransportResult(True, 200, "response received"),
    )

    exit_code = release_validation.main([
        "--commit-sha", "a1b2c3d4", "--staging", "--staging-url", "https://stage.example.test",
        "--advisory-command", "definitely-missing-release-scanner", "--deployment-evidence", str(deployment),
        "--rollback-evidence", str(rollback),
    ])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    scanner = checks_by_name(report)["dependency_advisories"]
    assert scanner["status"] == release_validation.STATUS_FAILED
    assert "scanner execution failed" in scanner["reason"]


def test_modes_and_model_cost_gate_before_work(monkeypatch) -> None:
    monkeypatch.setattr(
        release_validation,
        "run_advisory_commands",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    missing_mode = run_command("--commit-sha", "a1b2c3d4")
    both_modes = run_command("--commit-sha", "a1b2c3d4", "--self-test", "--staging")
    missing_cost = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "https://stage.example.test",
        "--model-chat-smoke",
    )

    reports = (missing_mode, both_modes, missing_cost)
    assert all(report["status"] == release_validation.STATUS_FAILED for report in reports)
    assert "exactly one mode" in checks_by_name(missing_mode)["configuration"]["reason"]
    assert "nonzero cost" in checks_by_name(missing_cost)["configuration"]["reason"]


def test_invalid_staging_url_or_chat_header_blocks_before_scanner(monkeypatch) -> None:
    def scanner_must_not_run(*_args, **_kwargs):
        raise AssertionError("invalid staging prerequisites must precede scanners")

    monkeypatch.setattr(release_validation, "run_advisory_commands", scanner_must_not_run)
    invalid_url = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "ftp://staging.example.test",
    )
    invalid_header = run_command(
        "--commit-sha",
        "a1b2c3d4",
        "--staging",
        "--staging-url",
        "https://staging.example.test",
        "--model-chat-smoke",
        "--chat-header",
        "Authorization: not-the-app-key",
        "--max-estimated-cost-usd",
        "0.01",
        "--estimated-cost-per-request-usd",
        "0.001",
    )

    assert "http or https" in checks_by_name(invalid_url)["configuration"]["reason"]
    assert "X-App-Key" in checks_by_name(invalid_header)["configuration"]["reason"]


def test_status_distinctions_and_nonzero_actionable_exit(capsys) -> None:
    assert release_validation.report_status([
        release_validation.check("pass", release_validation.STATUS_PASSED, "ok")
    ]) == release_validation.STATUS_PASSED
    assert release_validation.report_status([
        release_validation.check("na", release_validation.STATUS_NOT_APPLICABLE, "not configured")
    ]) == release_validation.STATUS_NOT_APPLICABLE
    assert release_validation.report_status([
        release_validation.check("blocked", release_validation.STATUS_BLOCKED, "provide evidence")
    ]) == release_validation.STATUS_BLOCKED
    assert release_validation.report_status([
        release_validation.check("failed", release_validation.STATUS_FAILED, "scanner exited nonzero")
    ]) == release_validation.STATUS_FAILED

    exit_code = release_validation.main(["--commit-sha", "not-a-sha", "--self-test"])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["status"] == release_validation.STATUS_FAILED
    assert "commit_sha" in output["checks"][0]["reason"]

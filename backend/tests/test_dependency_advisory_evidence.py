"""Focused tests for dependency advisory evidence generation and validation."""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from pathlib import Path

from scripts import build_dependency_advisory_evidence as builder
from scripts import release_advisory_policy as policy_module
from scripts import release_validation_advisories as advisories


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _arguments(
    root: Path,
    npm_full: object | None = None,
    npm_runtime: object | None = None,
    policy: object | None = None,
) -> object:
    reports = root / "reports"
    empty_npm = {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {"vulnerabilities": {"critical": 0, "high": 0, "moderate": 0, "low": 0, "info": 0, "total": 0}},
    }
    requirements = root / "backend/requirements.txt"
    development_requirements = root / "backend/requirements-dev.txt"
    requirements.parent.mkdir(parents=True, exist_ok=True)
    requirements.write_text("fastapi==0.129.0\n", encoding="utf-8")
    development_requirements.write_text("pytest==9.0.3\n", encoding="utf-8")
    lock = _write(root / "frontend/package-lock.json", {"lockfileVersion": 3, "packages": {}})
    policy_path = _write(
        root / "backend/release_advisory_exceptions.json",
        policy if policy is not None else {"schema_version": 1, "exceptions": []},
    )
    return builder.arguments(
        [
            "--commit-sha", "a1b2c3d4",
            "--npm-version", "10.9.2",
            "--pip-audit-version", "2.10.1",
            "--npm-full-report", str(_write(reports / "npm-full.json", npm_full or empty_npm)),
            "--npm-full-exit-code", "0" if npm_full is None else "1",
            "--npm-runtime-report", str(_write(reports / "npm-runtime.json", npm_runtime or empty_npm)),
            "--npm-runtime-exit-code", "0" if npm_runtime is None else "1",
            "--pip-runtime-report", str(_write(reports / "pip-runtime.json", {"dependencies": [], "fixes": []})),
            "--pip-runtime-exit-code", "0",
            "--pip-development-report", str(_write(reports / "pip-development.json", {"dependencies": [], "fixes": []})),
            "--pip-development-exit-code", "0",
            "--frontend-lock", str(lock),
            "--backend-requirements", str(requirements),
            "--backend-dev-requirements", str(development_requirements),
            "--exception-policy", str(policy_path),
            "--output", str(root / "evidence.json"),
        ]
    )


def _brace_report() -> dict[str, object]:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "brace-expansion": {
                "isDirect": False,
                "severity": "high",
                "nodes": ["node_modules/brace-expansion"],
                "via": [{"severity": "high", "url": "https://github.com/advisories/GHSA-mh99-v99m-4gvg"}],
            }
        },
        "metadata": {"vulnerabilities": {"critical": 0, "high": 1, "moderate": 0, "low": 0, "info": 0, "total": 1}},
    }


def _exception_policy(lock_path: Path, **changes: object) -> dict[str, object]:
    packages = [
        ("node_modules/eslint", "eslint", "9.39.4"),
        ("node_modules/eslint-plugin-import", "eslint-plugin-import", "2.32.0"),
        ("node_modules/eslint-plugin-jsx-a11y", "eslint-plugin-jsx-a11y", "6.10.2"),
        ("node_modules/eslint-plugin-react", "eslint-plugin-react", "7.37.5"),
        ("node_modules/minimatch", "minimatch", "3.1.5"),
        ("node_modules/brace-expansion", "brace-expansion", "1.1.16"),
    ]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"].update({path: {"name": name, "version": version} for path, name, version in packages})
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    exception: dict[str, object] = {
        "id": "eslint-brace-expansion-ghsa-mh99-v99m-4gvg",
        "advisory_id": "GHSA-mh99-v99m-4gvg",
        "package": "brace-expansion",
        "installed_versions": ["1.1.16"],
        "paths": ["node_modules/brace-expansion"],
        "scope": "development",
        "scan_id": "frontend_full",
        "scanner": "npm-audit",
        "lock": {"path": "frontend/package-lock.json", "sha256": sha256(lock_path.read_bytes()).hexdigest()},
        "expected_packages": [{"path": path, "name": name, "version": version} for path, name, version in packages],
        "rationale": "development-only ESLint chain",
        "expires_on": "2026-08-27",
    }
    exception.update(changes)
    return {"schema_version": 1, "exceptions": [exception]}


def _write_policy(args: object, value: object) -> None:
    assert isinstance(args.exception_policy, Path)
    args.exception_policy.write_text(json.dumps(value), encoding="utf-8")


def test_builder_and_parser_accept_complete_clean_candidate_evidence(tmp_path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    evidence = builder.build(args)
    args.output.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(advisories, "REPOSITORY_ROOT", tmp_path)

    result = advisories.advisory_evidence(str(args.output), "a1b2c3d4")

    assert evidence["status"] == "PASSED"
    assert result.status == "PASSED"
    assert result.evidence == {
        "required_scans": 4,
        "runtime_scans": 2,
        "development_scans": 2,
        "accepted_development_findings": 0,
    }


def test_builder_records_scanner_finding_and_validator_rejects_it(tmp_path, monkeypatch) -> None:
    npm_finding = {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "unsafe-package": {
                "isDirect": True,
                "severity": "high",
                "nodes": ["node_modules/unsafe-package"],
                "via": [{"severity": "high", "url": "https://github.com/advisories/GHSA-test-test-test"}],
            }
        },
        "metadata": {"vulnerabilities": {"critical": 0, "high": 1, "moderate": 0, "low": 0, "info": 0, "total": 1}},
    }
    args = _arguments(tmp_path, npm_finding)
    evidence = builder.build(args)
    args.output.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(advisories, "REPOSITORY_ROOT", tmp_path)

    result = advisories.advisory_evidence(str(args.output), "a1b2c3d4")

    assert evidence["status"] == "FAILED"
    assert evidence["scans"][0]["findings"][0]["advisory_id"] == "GHSA-test-test-test"
    assert result.status == "FAILED"


def test_exact_development_exception_is_preserved_and_passes(tmp_path, monkeypatch) -> None:
    args = _arguments(tmp_path, _brace_report())
    policy = _exception_policy(args.frontend_lock)
    _write_policy(args, policy)
    evidence = builder.build(args)
    args.output.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(advisories, "REPOSITORY_ROOT", tmp_path)

    result = advisories.advisory_evidence(str(args.output), "a1b2c3d4")

    assert evidence["status"] == "PASSED"
    assert evidence["scans"][0]["findings"]
    assert evidence["accepted_development_findings"] == [{
        "exception_id": "eslint-brace-expansion-ghsa-mh99-v99m-4gvg",
        "scan_id": "frontend_full",
        "scope": "development",
        "finding": evidence["scans"][0]["findings"][0],
    }]
    assert result.status == "PASSED"


def test_runtime_finding_cannot_use_development_exception(tmp_path) -> None:
    args = _arguments(tmp_path, _brace_report(), _brace_report())
    _write_policy(args, _exception_policy(args.frontend_lock))

    try:
        builder.build(args)
    except ValueError as exc:
        assert str(exc) == "runtime advisory findings cannot be accepted"
    else:
        raise AssertionError("runtime findings must never be accepted")


def test_expired_or_mismatched_exception_fails_closed(tmp_path) -> None:
    cases = [
        {"expires_on": "2026-07-27"},
        {"advisory_id": "GHSA-wrong-identity"},
        {"installed_versions": ["1.1.15"]},
        {"paths": ["node_modules/wrong-path"]},
    ]
    for index, changes in enumerate(cases):
        root = tmp_path / str(index)
        args = _arguments(root, _brace_report())
        _write_policy(args, _exception_policy(args.frontend_lock, **changes))
        try:
            builder.build(args)
        except ValueError:
            continue
        raise AssertionError("expired or mismatched exception must fail closed")


def test_builder_rejects_npm_metadata_that_claims_a_clean_report(tmp_path) -> None:
    forged_clean = {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {"vulnerabilities": {"critical": 0, "high": 1, "moderate": 0, "low": 0, "info": 0, "total": 1}},
    }

    try:
        builder.build(_arguments(tmp_path, forged_clean))
    except ValueError as exc:
        assert str(exc) == "npm report metadata does not match vulnerabilities"
    else:
        raise AssertionError("inconsistent npm metadata must fail closed")


def test_malformed_exception_policy_top_level_is_controlled_failure(tmp_path) -> None:
    args = _arguments(tmp_path)
    assert isinstance(args.exception_policy, Path)
    args.exception_policy.write_text("[]", encoding="utf-8")

    try:
        builder.build(args)
    except ValueError as exc:
        assert str(exc) == "advisory exception policy schema is invalid"
    else:
        raise AssertionError("non-object policy must fail closed")


def test_pip_audit_v2_envelope_preserves_direct_finding_and_fix() -> None:
    findings = builder._pip_findings(
        {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.32.4",
                    "vulns": [{"id": "PYSEC-2026-2275", "fix_versions": ["2.33.0"]}],
                }
            ],
            "fixes": [],
        },
        {"requests"},
    )

    assert findings == [{
        "advisory_id": "PYSEC-2026-2275",
        "package": "requests",
        "installed_versions": ["2.32.4"],
        "severity": "unknown",
        "direct": True,
        "paths": [],
        "fixed_versions": ["2.33.0"],
    }]


def test_parser_rejects_evidence_for_changed_dependency_input(tmp_path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    args.output.write_text(json.dumps(builder.build(args)), encoding="utf-8")
    (tmp_path / "frontend/package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(advisories, "REPOSITORY_ROOT", tmp_path)

    result = advisories.advisory_evidence(str(args.output), "a1b2c3d4")

    assert result.status == "FAILED"
    assert result.reason == "dependency advisory input digest does not match"


def test_exception_lock_digest_and_duplicate_or_unmatched_entries_fail_closed(tmp_path) -> None:
    args = _arguments(tmp_path, _brace_report())
    valid_policy = _exception_policy(args.frontend_lock)
    _write_policy(args, valid_policy)
    lock = json.loads(args.frontend_lock.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/brace-expansion"]["version"] = "1.1.17"
    args.frontend_lock.write_text(json.dumps(lock), encoding="utf-8")
    try:
        builder.build(args)
    except ValueError as exc:
        assert str(exc) == "advisory exception lock digest does not match"
    else:
        raise AssertionError("changed lock digest must fail closed")

    duplicate_root = tmp_path / "duplicate"
    duplicate_args = _arguments(duplicate_root, _brace_report())
    duplicate_policy = _exception_policy(duplicate_args.frontend_lock)
    duplicate_policy["exceptions"].append(dict(duplicate_policy["exceptions"][0]))
    _write_policy(duplicate_args, duplicate_policy)
    try:
        builder.build(duplicate_args)
    except ValueError as exc:
        assert str(exc) == "advisory findings may have only one exception"
    else:
        raise AssertionError("duplicate exception must fail closed")

    unmatched_root = tmp_path / "unmatched"
    unmatched_args = _arguments(unmatched_root, _brace_report())
    unmatched_policy = _exception_policy(unmatched_args.frontend_lock, advisory_id="GHSA-unmatched-entry")
    _write_policy(unmatched_args, unmatched_policy)
    try:
        builder.build(unmatched_args)
    except ValueError as exc:
        assert str(exc) == "advisory exception must match exactly one development finding"
    else:
        raise AssertionError("unmatched exception must fail closed")


def test_exception_expires_on_its_first_invalid_utc_day(tmp_path) -> None:
    args = _arguments(tmp_path, _brace_report())
    policy = _exception_policy(args.frontend_lock)
    _write_policy(args, policy)
    evidence = builder.build(args)
    scans = {str(scan["id"]): scan for scan in evidence["scans"]}

    try:
        policy_module.accepted_development_findings(
            scans,
            policy,
            tmp_path,
            today=date(2026, 8, 27),
        )
    except ValueError as exc:
        assert str(exc) == "advisory exception has expired"
    else:
        raise AssertionError("expiry boundary must fail closed")


def test_parser_rejects_unexpected_scanner_format(tmp_path, monkeypatch) -> None:
    args = _arguments(tmp_path)
    evidence = builder.build(args)
    evidence["scans"][0]["scanner"]["format"] = "forged-format"
    args.output.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(advisories, "REPOSITORY_ROOT", tmp_path)

    result = advisories.advisory_evidence(str(args.output), "a1b2c3d4")

    assert result.status == "FAILED"
    assert result.reason == "dependency advisory scan details are invalid"

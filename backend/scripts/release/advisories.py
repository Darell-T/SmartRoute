"""Strict parser for candidate-bound dependency advisory evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.release.advisory_policy import (
    POLICY_RELATIVE_PATH,
    accepted_development_findings,
    load_policy,
    policy_evidence,
)
from scripts.release.transport import ExternalCheckResult

SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_SCANS = (
    ("frontend_full", "npm-audit", "npm-audit-v2-json", "build_and_runtime", "frontend/package-lock.json"),
    ("frontend_runtime", "npm-audit", "npm-audit-v2-json", "runtime", "frontend/package-lock.json"),
    ("backend_runtime", "pip-audit", "pip-audit-2-json", "runtime", "backend/requirements.txt"),
    ("backend_development", "pip-audit", "pip-audit-2-json", "development", "backend/requirements-dev.txt"),
)
SUMMARY_KEYS = ("critical", "high", "moderate", "low", "info", "unknown", "total")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invalid(reason: str) -> ExternalCheckResult:
    return ExternalCheckResult("FAILED", reason, {})


def _summary_matches(summary: dict[str, object], findings: list[object]) -> bool:
    counts = dict.fromkeys(SUMMARY_KEYS[:-1], 0)
    for finding in findings:
        if not isinstance(finding, dict):
            return False
        severity = finding.get("severity")
        if severity not in counts:
            return False
        counts[severity] += 1
    counts["total"] = len(findings)
    return summary == counts


def _candidate_matches(candidate: object, candidate_sha: str) -> bool:
    if not isinstance(candidate, dict):
        return False
    evidence_sha = candidate.get("commit_sha")
    return (
        isinstance(evidence_sha, str)
        and bool(SHA_PATTERN.fullmatch(candidate_sha))
        and bool(SHA_PATTERN.fullmatch(evidence_sha))
        and evidence_sha.casefold() == candidate_sha.casefold()
    )


def _load_passed_evidence(path: str | None) -> ExternalCheckResult | dict[str, object]:
    if not path:
        return ExternalCheckResult("BLOCKED", "dependency advisory evidence was not supplied", {})
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _invalid("dependency advisory evidence could not be read")
    candidate = value.get("candidate") if isinstance(value, dict) else None
    scans = value.get("scans") if isinstance(value, dict) else None
    policy = value.get("policy") if isinstance(value, dict) else None
    accepted = value.get("accepted_development_findings") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("status") != "PASSED"
        or not isinstance(candidate, dict)
        or not isinstance(scans, list)
        or not isinstance(policy, dict)
        or not isinstance(accepted, list)
    ):
        if not isinstance(value, dict):
            return _invalid("dependency advisory evidence must be an object")
        return _invalid("dependency advisory evidence schema does not match")
    return value


def _index_scans(scans: list[object]) -> ExternalCheckResult | dict[str, object]:
    by_id: dict[str, object] = {}
    for scan in scans:
        if not isinstance(scan, dict) or not isinstance(scan.get("id"), str):
            return _invalid("dependency advisory scan entry is invalid")
        scan_id = scan["id"]
        if scan_id in by_id:
            return _invalid("dependency advisory scan identifiers must be unique")
        by_id[scan_id] = scan
    if set(by_id) != {item[0] for item in REQUIRED_SCANS}:
        return _invalid("dependency advisory evidence has incomplete scanner coverage")
    return by_id


def _scan_details_valid(scan: dict[str, object], required: tuple[str, str, str, str, str]) -> bool:
    _scan_id, scanner_name, scanner_format, scope, relative_input = required
    scanner = scan.get("scanner")
    scanner_input = scan.get("input")
    summary = scan.get("summary")
    findings = scan.get("findings")
    return (
        isinstance(scanner, dict)
        and scanner.get("name") == scanner_name
        and isinstance(scanner.get("version"), str)
        and bool(scanner["version"].strip())
        and scanner.get("format") == scanner_format
        and scan.get("scope") == scope
        and isinstance(scanner_input, dict)
        and scanner_input.get("path") == relative_input
        and isinstance(scanner_input.get("sha256"), str)
        and bool(DIGEST_PATTERN.fullmatch(scanner_input["sha256"]))
        and isinstance(summary, dict)
        and isinstance(findings, list)
        and scan.get("exit_code") == (1 if findings else 0)
        and _summary_matches(summary, findings)
    )


def _verify_scans(by_id: dict[str, object]) -> ExternalCheckResult | None:
    for required in REQUIRED_SCANS:
        scan = by_id[required[0]]
        assert isinstance(scan, dict)
        if not _scan_details_valid(scan, required):
            return _invalid("dependency advisory scan details are invalid")
        scanner_input = scan.get("input")
        assert isinstance(scanner_input, dict)
        try:
            current_digest = _sha256(REPOSITORY_ROOT / required[4])
        except OSError:
            return _invalid("dependency advisory input could not be verified")
        if scanner_input["sha256"] != current_digest:
            return _invalid("dependency advisory input digest does not match")
    return None


def _verify_policy(
    by_id: dict[str, object],
    policy: object,
    accepted: object,
) -> ExternalCheckResult | None:
    policy_path = REPOSITORY_ROOT / POLICY_RELATIVE_PATH
    try:
        current_policy = load_policy(policy_path)
        expected_policy = policy_evidence(policy_path)
        expected_accepted = accepted_development_findings(by_id, current_policy, REPOSITORY_ROOT)
    except (OSError, ValueError):
        return _invalid("dependency advisory exception policy is invalid")
    if policy != expected_policy or accepted != expected_accepted:
        return _invalid("dependency advisory exceptions do not match policy")
    if not isinstance(accepted, list):
        return _invalid("dependency advisory exceptions are malformed")
    accepted_keys = {
        (item["scan_id"], json.dumps(item["finding"], sort_keys=True, separators=(",", ":")))
        for item in accepted
        if isinstance(item, dict) and isinstance(item.get("scan_id"), str) and isinstance(item.get("finding"), dict)
    }
    if len(accepted_keys) != len(accepted):
        return _invalid("dependency advisory exceptions are malformed")
    unresolved = [
        finding
        for scan in by_id.values()
        if isinstance(scan, dict)
        for finding in scan["findings"]
        if (scan["id"], json.dumps(finding, sort_keys=True, separators=(",", ":"))) not in accepted_keys
    ]
    if unresolved:
        return _invalid("dependency advisory evidence contains open findings")
    return None


def advisory_evidence(path: str | None, candidate_sha: str) -> ExternalCheckResult:
    """Accept only complete, zero-finding evidence for the current candidate inputs."""

    value = _load_passed_evidence(path)
    if isinstance(value, ExternalCheckResult):
        return value
    if not _candidate_matches(value.get("candidate"), candidate_sha):
        return _invalid("dependency advisory evidence candidate SHA is invalid")
    scans = value["scans"]
    assert isinstance(scans, list)
    by_id = _index_scans(scans)
    if isinstance(by_id, ExternalCheckResult):
        return by_id
    scan_error = _verify_scans(by_id)
    if scan_error is not None:
        return scan_error
    policy_error = _verify_policy(by_id, value.get("policy"), value.get("accepted_development_findings"))
    if policy_error is not None:
        return policy_error
    accepted = value.get("accepted_development_findings")
    accepted_count = len(accepted) if isinstance(accepted, list) else 0
    return ExternalCheckResult(
        "PASSED",
        "npm and pip dependency advisory scans passed for current release inputs",
        {
            "required_scans": len(REQUIRED_SCANS),
            "runtime_scans": 2,
            "development_scans": 2,
            "accepted_development_findings": accepted_count,
        },
    )

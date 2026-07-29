"""Strict parser for candidate-bound dependency advisory evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts.release_advisory_policy import (
    POLICY_RELATIVE_PATH,
    accepted_development_findings,
    load_policy,
    policy_evidence,
)
from scripts.release_validation_transport import ExternalCheckResult


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
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
    counts = {key: 0 for key in SUMMARY_KEYS[:-1]}
    for finding in findings:
        if not isinstance(finding, dict):
            return False
        severity = finding.get("severity")
        if severity not in counts:
            return False
        counts[severity] += 1
    counts["total"] = len(findings)
    return summary == counts


def advisory_evidence(path: str | None, candidate_sha: str) -> ExternalCheckResult:
    """Accept only complete, zero-finding evidence for the current candidate inputs."""

    if not path:
        return ExternalCheckResult("BLOCKED", "dependency advisory evidence was not supplied", {})
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _invalid("dependency advisory evidence could not be read")
    if not isinstance(value, dict):
        return _invalid("dependency advisory evidence must be an object")
    candidate = value.get("candidate")
    scans = value.get("scans")
    policy = value.get("policy")
    accepted = value.get("accepted_development_findings")
    if (
        value.get("schema_version") != 1
        or value.get("status") != "PASSED"
        or not isinstance(candidate, dict)
        or not isinstance(scans, list)
        or not isinstance(policy, dict)
        or not isinstance(accepted, list)
    ):
        return _invalid("dependency advisory evidence schema does not match")
    evidence_sha = candidate.get("commit_sha")
    if (
        not isinstance(evidence_sha, str)
        or not SHA_PATTERN.fullmatch(candidate_sha)
        or not SHA_PATTERN.fullmatch(evidence_sha)
        or evidence_sha.casefold() != candidate_sha.casefold()
    ):
        return _invalid("dependency advisory evidence candidate SHA is invalid")

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

    for scan_id, scanner_name, scanner_format, scope, relative_input in REQUIRED_SCANS:
        scan = by_id[scan_id]
        assert isinstance(scan, dict)
        scanner = scan.get("scanner")
        scanner_input = scan.get("input")
        summary = scan.get("summary")
        findings = scan.get("findings")
        if (
            not isinstance(scanner, dict)
            or scanner.get("name") != scanner_name
            or not isinstance(scanner.get("version"), str)
            or not scanner["version"].strip()
            or scanner.get("format") != scanner_format
            or scan.get("scope") != scope
            or not isinstance(scanner_input, dict)
            or scanner_input.get("path") != relative_input
            or not isinstance(scanner_input.get("sha256"), str)
            or not DIGEST_PATTERN.fullmatch(scanner_input["sha256"])
            or not isinstance(summary, dict)
            or not isinstance(findings, list)
            or scan.get("exit_code") != (1 if findings else 0)
            or not _summary_matches(summary, findings)
        ):
            return _invalid("dependency advisory scan details are invalid")
        try:
            current_digest = _sha256(REPOSITORY_ROOT / relative_input)
        except OSError:
            return _invalid("dependency advisory input could not be verified")
        if scanner_input["sha256"] != current_digest:
            return _invalid("dependency advisory input digest does not match")
    policy_path = REPOSITORY_ROOT / POLICY_RELATIVE_PATH
    try:
        current_policy = load_policy(policy_path)
        expected_policy = policy_evidence(policy_path)
        expected_accepted = accepted_development_findings(by_id, current_policy, REPOSITORY_ROOT)
    except (OSError, ValueError):
        return _invalid("dependency advisory exception policy is invalid")
    if policy != expected_policy or accepted != expected_accepted:
        return _invalid("dependency advisory exceptions do not match policy")
    accepted_keys = {
        (item["scan_id"], json.dumps(item["finding"], sort_keys=True, separators=(",", ":")))
        for item in accepted
        if isinstance(item, dict) and isinstance(item.get("scan_id"), str) and isinstance(item.get("finding"), dict)
    }
    if len(accepted_keys) != len(accepted):
        return _invalid("dependency advisory exceptions are malformed")
    unresolved = [
        finding
        for scan in scans
        for finding in scan["findings"]
        if (scan["id"], json.dumps(finding, sort_keys=True, separators=(",", ":"))) not in accepted_keys
    ]
    if unresolved:
        return _invalid("dependency advisory evidence contains open findings")
    return ExternalCheckResult(
        "PASSED",
        "npm and pip dependency advisory scans passed for current release inputs",
        {"required_scans": len(REQUIRED_SCANS), "runtime_scans": 2, "development_scans": 2, "accepted_development_findings": len(accepted)},
    )

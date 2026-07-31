"""Lock-bound, expiring development-advisory exception policy."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path


POLICY_RELATIVE_PATH = "backend/release_advisory_exceptions.json"
LOCK_RELATIVE_PATH = "frontend/package-lock.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_sha256(path: Path) -> str:
    """Hash lockfile content consistently across platform line endings."""
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _package_name(path: str, value: dict[str, object]) -> str:
    name = value.get("name")
    if isinstance(name, str) and name:
        return name
    return path.rsplit("node_modules/", 1)[-1]


def load_policy(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("advisory exception policy could not be read") from exc
    exceptions = value.get("exceptions") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(exceptions, list):
        raise ValueError("advisory exception policy schema is invalid")
    return value


def policy_evidence(path: Path) -> dict[str, str]:
    return {"path": POLICY_RELATIVE_PATH, "sha256": sha256(path)}


def _matching_findings(scan: dict[str, object], exception: dict[str, object]) -> list[dict[str, object]]:
    findings = scan.get("findings")
    if not isinstance(findings, list):
        return []
    return [
        finding for finding in findings
        if isinstance(finding, dict)
        and finding.get("advisory_id") == exception.get("advisory_id")
        and finding.get("package") == exception.get("package")
        and finding.get("installed_versions") == exception.get("installed_versions")
        and finding.get("paths") == exception.get("paths")
    ]


def _validate_exception(exception: dict[str, object], repository_root: Path, today: date) -> None:
    required = ("id", "advisory_id", "package", "installed_versions", "paths", "rationale", "expires_on")
    scalar_keys = tuple(key for key in required if key not in {"installed_versions", "paths"})
    if any(not isinstance(exception.get(key), str) or not exception[key] for key in scalar_keys):
        raise ValueError("advisory exception identity is invalid")
    if (
        exception.get("scope") != "development"
        or exception.get("scan_id") != "frontend_full"
        or exception.get("scanner") != "npm-audit"
    ):
        raise ValueError("advisory exception scope is invalid")
    installed_versions = exception.get("installed_versions")
    paths = exception.get("paths")
    if not isinstance(installed_versions, list) or not installed_versions or not all(isinstance(item, str) for item in installed_versions):
        raise ValueError("advisory exception installed versions are invalid")
    if not isinstance(paths, list) or not paths or not all(isinstance(item, str) for item in paths):
        raise ValueError("advisory exception paths are invalid")
    try:
        expires_on = date.fromisoformat(str(exception["expires_on"]))
    except ValueError as exc:
        raise ValueError("advisory exception expiry is invalid") from exc
    if today >= expires_on:
        raise ValueError("advisory exception has expired")
    lock = exception.get("lock")
    if not isinstance(lock, dict) or lock.get("path") != LOCK_RELATIVE_PATH:
        raise ValueError("advisory exception lock identity is invalid")
    lock_path = repository_root / LOCK_RELATIVE_PATH
    if lock.get("sha256") != lock_sha256(lock_path):
        raise ValueError("advisory exception lock digest does not match")
    expected_packages = exception.get("expected_packages")
    if not isinstance(expected_packages, list) or not expected_packages:
        raise ValueError("advisory exception dependency chain is invalid")
    try:
        lock_value = json.loads(lock_path.read_text(encoding="utf-8"))
        packages = lock_value["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("advisory exception lock could not be inspected") from exc
    for expected in expected_packages:
        if not isinstance(expected, dict):
            raise ValueError("advisory exception dependency identity is invalid")
        path = expected.get("path")
        value = packages.get(path) if isinstance(path, str) and isinstance(packages, dict) else None
        if (
            not isinstance(value, dict)
            or _package_name(path, value) != expected.get("name")
            or value.get("version") != expected.get("version")
        ):
            raise ValueError("advisory exception dependency chain does not match")


def accepted_development_findings(
    scans: dict[str, dict[str, object]],
    policy: dict[str, object],
    repository_root: Path,
    today: date | None = None,
) -> list[dict[str, object]]:
    current_day = today or datetime.now(UTC).date()
    full_scan = scans.get("frontend_full")
    runtime_scan = scans.get("frontend_runtime")
    exceptions = policy["exceptions"]
    if not isinstance(full_scan, dict) or not isinstance(runtime_scan, dict) or not isinstance(exceptions, list):
        raise ValueError("advisory exception scanner coverage is invalid")
    accepted: list[dict[str, object]] = []
    accepted_keys: set[str] = set()
    for exception in exceptions:
        if not isinstance(exception, dict):
            raise ValueError("advisory exception entry is invalid")
        _validate_exception(exception, repository_root, current_day)
        matches = _matching_findings(full_scan, exception)
        if len(matches) != 1:
            raise ValueError("advisory exception must match exactly one development finding")
        if _matching_findings(runtime_scan, exception):
            raise ValueError("runtime advisory findings cannot be accepted")
        finding = matches[0]
        key = json.dumps(finding, sort_keys=True, separators=(",", ":"))
        if key in accepted_keys:
            raise ValueError("advisory findings may have only one exception")
        accepted_keys.add(key)
        accepted.append({"exception_id": exception["id"], "scan_id": "frontend_full", "scope": "development", "finding": finding})
    return accepted

"""Lock-bound, expiring development-advisory exception policy."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NoReturn

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


def _invalid(message: str) -> NoReturn:
    raise ValueError(message)


def load_policy(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        unreadable = "advisory exception policy could not be read"
        raise ValueError(unreadable) from exc
    exceptions = value.get("exceptions") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(exceptions, list):
        _invalid("advisory exception policy schema is invalid")
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


def _require_identity(exception: dict[str, object]) -> None:
    required = ("id", "advisory_id", "package", "installed_versions", "paths", "rationale", "expires_on")
    scalar_keys = tuple(key for key in required if key not in {"installed_versions", "paths"})
    if any(not isinstance(exception.get(key), str) or not exception[key] for key in scalar_keys):
        _invalid("advisory exception identity is invalid")
    if (
        exception.get("scope") != "development"
        or exception.get("scan_id") != "frontend_full"
        or exception.get("scanner") != "npm-audit"
    ):
        _invalid("advisory exception scope is invalid")


def _require_string_list(value: object, message: str) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        _invalid(message)


def _require_unexpired(exception: dict[str, object], today: date) -> None:
    try:
        expires_on = date.fromisoformat(str(exception["expires_on"]))
    except ValueError as exc:
        invalid_expiry = "advisory exception expiry is invalid"
        raise ValueError(invalid_expiry) from exc
    if today >= expires_on:
        _invalid("advisory exception has expired")


def _require_lock(exception: dict[str, object], repository_root: Path) -> Path:
    lock = exception.get("lock")
    if not isinstance(lock, dict) or lock.get("path") != LOCK_RELATIVE_PATH:
        _invalid("advisory exception lock identity is invalid")
    lock_path = repository_root / LOCK_RELATIVE_PATH
    if lock.get("sha256") != lock_sha256(lock_path):
        _invalid("advisory exception lock digest does not match")
    return lock_path


def _lock_packages(lock_path: Path) -> object:
    try:
        lock_value = json.loads(lock_path.read_text(encoding="utf-8"))
        return lock_value["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        unreadable = "advisory exception lock could not be inspected"
        raise ValueError(unreadable) from exc


def _package_matches(expected: object, packages: object) -> bool:
    if not isinstance(expected, dict):
        return False
    path = expected.get("path")
    value = packages.get(path) if isinstance(path, str) and isinstance(packages, dict) else None
    return (
        isinstance(value, dict)
        and _package_name(path, value) == expected.get("name")
        and value.get("version") == expected.get("version")
    )


def _require_dependency_chain(exception: dict[str, object], lock_path: Path) -> None:
    expected_packages = exception.get("expected_packages")
    if not isinstance(expected_packages, list) or not expected_packages:
        _invalid("advisory exception dependency chain is invalid")
    packages = _lock_packages(lock_path)
    if any(not _package_matches(expected, packages) for expected in expected_packages):
        _invalid("advisory exception dependency chain does not match")


def _validate_exception(exception: dict[str, object], repository_root: Path, today: date) -> None:
    _require_identity(exception)
    _require_string_list(exception.get("installed_versions"), "advisory exception installed versions are invalid")
    _require_string_list(exception.get("paths"), "advisory exception paths are invalid")
    _require_unexpired(exception, today)
    lock_path = _require_lock(exception, repository_root)
    _require_dependency_chain(exception, lock_path)


def _accept_one_exception(
    exception: object,
    full_scan: dict[str, object],
    runtime_scan: dict[str, object],
    repository_root: Path,
    current_day: date,
    accepted_keys: set[str],
) -> dict[str, object]:
    if not isinstance(exception, dict):
        _invalid("advisory exception entry is invalid")
    _validate_exception(exception, repository_root, current_day)
    matches = _matching_findings(full_scan, exception)
    if len(matches) != 1:
        _invalid("advisory exception must match exactly one development finding")
    if _matching_findings(runtime_scan, exception):
        _invalid("runtime advisory findings cannot be accepted")
    finding = matches[0]
    key = json.dumps(finding, sort_keys=True, separators=(",", ":"))
    if key in accepted_keys:
        _invalid("advisory findings may have only one exception")
    accepted_keys.add(key)
    return {
        "exception_id": exception["id"],
        "scan_id": "frontend_full",
        "scope": "development",
        "finding": finding,
    }


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
        _invalid("advisory exception scanner coverage is invalid")
    accepted_keys: set[str] = set()
    return [
        _accept_one_exception(
            exception, full_scan, runtime_scan, repository_root, current_day, accepted_keys
        )
        for exception in exceptions
    ]

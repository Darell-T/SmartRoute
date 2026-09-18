"""Strict parser for sanitized Playwright release evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.release.transport import ExternalCheckResult

SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
REQUIRED_CASES = ["chat", "quick_mode", "map_handoff", "accessibility", "shell", "zoom"]
VISUAL_SCOPE = "platform_local_not_certified_in_linux_ci"


def _schema_matches(value: dict[str, object], candidate: object, projects: object, visual: object) -> bool:
    return (
        value.get("schema_version") == 1
        and value.get("status") == "PASSED"
        and value.get("runner") == "playwright"
        and isinstance(candidate, dict)
        and isinstance(projects, dict)
        and isinstance(visual, dict)
        and visual.get("certified") is False
        and visual.get("scope") == VISUAL_SCOPE
        and value.get("required_cases") == REQUIRED_CASES
    )


def _candidate_matches(candidate: dict[str, object], candidate_sha: str) -> bool:
    evidence_sha = candidate.get("commit_sha")
    return (
        isinstance(evidence_sha, str)
        and bool(SHA_PATTERN.fullmatch(candidate_sha))
        and bool(SHA_PATTERN.fullmatch(evidence_sha))
        and evidence_sha.casefold() == candidate_sha.casefold()
    )


def _coverage_matches(desktop: dict[str, object], mobile: dict[str, object]) -> bool:
    return (
        desktop.get("passed_required_cases") == REQUIRED_CASES
        and desktop.get("expected_skipped_cases") == []
        and mobile.get("passed_required_cases") == REQUIRED_CASES[:-1]
        and mobile.get("expected_skipped_cases") == ["zoom"]
    )


def _read_browser_object(path: str | None) -> ExternalCheckResult | dict[str, object]:
    if not path:
        return ExternalCheckResult("BLOCKED", "browser and accessibility evidence was not supplied", {})
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ExternalCheckResult("FAILED", "browser evidence could not be read", {})
    if not isinstance(value, dict):
        return ExternalCheckResult("FAILED", "browser evidence must be an object", {})
    return value


def browser_evidence(path: str | None, candidate_sha: str) -> ExternalCheckResult:
    """Accept only complete, candidate-bound Playwright evidence."""

    value = _read_browser_object(path)
    if isinstance(value, ExternalCheckResult):
        return value
    candidate = value.get("candidate")
    projects = value.get("projects")
    visual = value.get("visual_comparison")
    if not _schema_matches(value, candidate, projects, visual):
        return ExternalCheckResult("FAILED", "browser evidence schema or candidate does not match", {})
    assert isinstance(candidate, dict)
    assert isinstance(projects, dict)
    if not _candidate_matches(candidate, candidate_sha):
        return ExternalCheckResult("FAILED", "browser evidence candidate SHA is invalid", {})
    desktop = projects.get("desktop")
    mobile = projects.get("mobile")
    if not isinstance(desktop, dict) or not isinstance(mobile, dict):
        return ExternalCheckResult("FAILED", "browser evidence project coverage is invalid", {})
    if not _coverage_matches(desktop, mobile):
        return ExternalCheckResult("FAILED", "browser evidence required coverage is incomplete", {})
    return ExternalCheckResult(
        "PASSED",
        "Playwright desktop and mobile browser/accessibility coverage passed",
        {
            "runner": "playwright",
            "desktop_required_cases": len(REQUIRED_CASES),
            "mobile_required_cases": len(REQUIRED_CASES) - 1,
            "mobile_expected_skips": 1,
            "visual_comparison": VISUAL_SCOPE,
        },
    )

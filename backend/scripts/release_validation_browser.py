"""Strict parser for sanitized Playwright release evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.release_validation_transport import ExternalCheckResult


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
REQUIRED_CASES = ["chat", "quick_mode", "map_handoff", "accessibility", "shell", "zoom"]
VISUAL_SCOPE = "platform_local_not_certified_in_linux_ci"


def browser_evidence(path: str | None, candidate_sha: str) -> ExternalCheckResult:
    """Accept only complete, candidate-bound Playwright evidence."""

    if not path:
        return ExternalCheckResult("BLOCKED", "browser and accessibility evidence was not supplied", {})
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ExternalCheckResult("FAILED", "browser evidence could not be read", {})
    if not isinstance(value, dict):
        return ExternalCheckResult("FAILED", "browser evidence must be an object", {})
    candidate = value.get("candidate")
    projects = value.get("projects")
    visual = value.get("visual_comparison")
    if (
        value.get("schema_version") != 1
        or value.get("status") != "PASSED"
        or value.get("runner") != "playwright"
        or not isinstance(candidate, dict)
        or not isinstance(projects, dict)
        or not isinstance(visual, dict)
        or visual.get("certified") is not False
        or visual.get("scope") != VISUAL_SCOPE
        or value.get("required_cases") != REQUIRED_CASES
    ):
        return ExternalCheckResult("FAILED", "browser evidence schema or candidate does not match", {})
    evidence_sha = candidate.get("commit_sha")
    if (
        not isinstance(evidence_sha, str)
        or not SHA_PATTERN.fullmatch(candidate_sha)
        or not SHA_PATTERN.fullmatch(evidence_sha)
        or evidence_sha.casefold() != candidate_sha.casefold()
    ):
        return ExternalCheckResult("FAILED", "browser evidence candidate SHA is invalid", {})
    desktop = projects.get("desktop")
    mobile = projects.get("mobile")
    if not isinstance(desktop, dict) or not isinstance(mobile, dict):
        return ExternalCheckResult("FAILED", "browser evidence project coverage is invalid", {})
    if (
        desktop.get("passed_required_cases") != REQUIRED_CASES
        or desktop.get("expected_skipped_cases") != []
        or mobile.get("passed_required_cases") != REQUIRED_CASES[:-1]
        or mobile.get("expected_skipped_cases") != ["zoom"]
    ):
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

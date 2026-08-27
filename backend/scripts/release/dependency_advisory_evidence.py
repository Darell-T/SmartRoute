"""Build sanitized dependency-advisory evidence from npm and pip scanner JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from .advisory_policy import (
    accepted_development_findings,
    load_policy,
    policy_evidence,
)

NPM_SEVERITIES = ("critical", "high", "moderate", "low", "info")
SEVERITIES = (*NPM_SEVERITIES, "unknown")
ADVISORY_ID = re.compile(r"(?:GHSA-[A-Za-z0-9-]+|CVE-\d{4}-\d+)")
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invalid(message: str) -> NoReturn:
    raise ValueError(message)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        unreadable = f"scanner report could not be read: {path}"
        raise ValueError(unreadable) from exc


def _read_requirements(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = REQUIREMENT_NAME.match(stripped)
        if not match:
            _invalid(f"unsupported requirement syntax in {path.name}")
        names.add(match.group(1).replace("_", "-").casefold())
    return names


def _summary(findings: list[dict[str, object]]) -> dict[str, int]:
    totals = dict.fromkeys(SEVERITIES, 0)
    for finding in findings:
        severity = finding["severity"]
        assert isinstance(severity, str)
        totals[severity] += 1
    totals["total"] = len(findings)
    return totals


def _npm_lock_packages(lock_path: Path) -> dict[str, object]:
    lock = _read_json(lock_path)
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        _invalid("frontend lock has no packages object")
    return packages


def _npm_report_maps(value: object) -> tuple[dict[str, object], dict[str, object]]:
    if not isinstance(value, dict) or value.get("auditReportVersion") != 2:
        _invalid("npm report must use audit report version 2")
    vulnerabilities = value.get("vulnerabilities")
    metadata = value.get("metadata")
    metadata_vulnerabilities = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(vulnerabilities, dict) or not isinstance(metadata_vulnerabilities, dict):
        _invalid("npm report has no vulnerabilities object")
    return vulnerabilities, metadata_vulnerabilities


def _npm_package_paths(details: dict[str, object]) -> tuple[bool, str, list[str]]:
    direct = details.get("isDirect")
    nodes = details.get("nodes")
    via = details.get("via")
    package_severity = details.get("severity")
    if (
        not isinstance(direct, bool)
        or not isinstance(nodes, list)
        or not isinstance(via, list)
        or not isinstance(package_severity, str)
        or package_severity not in NPM_SEVERITIES
    ):
        _invalid("npm vulnerability details are invalid")
    paths = [node for node in nodes if isinstance(node, str)]
    if len(paths) != len(nodes):
        _invalid("npm vulnerability paths are invalid")
    return direct, package_severity, paths


def _npm_versions(paths: list[str], packages: dict[str, object]) -> list[str]:
    return sorted(
        {
            entry["version"]
            for path in paths
            for entry in [packages.get(path)]
            if isinstance(entry, dict) and isinstance(entry.get("version"), str)
        }
    )


def _npm_advisory_finding(
    package: str,
    details: dict[str, object],
    advisory: object,
    packages: dict[str, object],
) -> dict[str, object] | None:
    if not isinstance(advisory, dict):
        return None
    direct, _package_severity, paths = _npm_package_paths(details)
    severity = advisory.get("severity")
    url = advisory.get("url")
    if not isinstance(severity, str) or severity not in SEVERITIES or not isinstance(url, str):
        _invalid("npm advisory details are invalid")
    identifier = ADVISORY_ID.search(url)
    if identifier is None:
        _invalid("npm advisory has no recognized identifier")
    fix = details.get("fixAvailable")
    fixed_versions = [fix["version"]] if isinstance(fix, dict) and isinstance(fix.get("version"), str) else []
    return {
        "advisory_id": identifier.group(0),
        "package": package,
        "installed_versions": _npm_versions(paths, packages),
        "severity": severity,
        "direct": direct,
        "paths": paths,
        "fixed_versions": fixed_versions,
    }


def _npm_findings(value: object, lock_path: Path) -> list[dict[str, object]]:
    vulnerabilities, metadata_vulnerabilities = _npm_report_maps(value)
    packages = _npm_lock_packages(lock_path)
    findings: list[dict[str, object]] = []
    package_severities = dict.fromkeys(NPM_SEVERITIES, 0)
    for package, details in vulnerabilities.items():
        if not isinstance(package, str) or not isinstance(details, dict):
            _invalid("npm vulnerability entry is invalid")
        _direct, package_severity, _paths = _npm_package_paths(details)
        package_severities[package_severity] += 1
        via = details.get("via")
        assert isinstance(via, list)
        for advisory in via:
            finding = _npm_advisory_finding(package, details, advisory, packages)
            if finding is not None:
                findings.append(finding)
    expected_metadata = {**package_severities, "total": len(vulnerabilities)}
    if any(metadata_vulnerabilities.get(key) != count for key, count in expected_metadata.items()):
        _invalid("npm report metadata does not match vulnerabilities")
    return findings


def _pip_advisory_finding(
    name: str,
    version: str,
    requirements: set[str],
    advisory: object,
) -> dict[str, object]:
    if not isinstance(advisory, dict):
        _invalid("pip-audit advisory entry is invalid")
    identifier = advisory.get("id")
    fixes = advisory.get("fix_versions")
    if not isinstance(identifier, str) or not identifier or not isinstance(fixes, list):
        _invalid("pip-audit advisory details are invalid")
    fixed_versions = [item for item in fixes if isinstance(item, str)]
    if len(fixed_versions) != len(fixes):
        _invalid("pip-audit fix versions are invalid")
    return {
        "advisory_id": identifier,
        "package": name,
        "installed_versions": [version],
        "severity": "unknown",
        "direct": name.replace("_", "-").casefold() in requirements,
        "paths": [],
        "fixed_versions": fixed_versions,
    }


def _pip_findings(value: object, requirements: set[str]) -> list[dict[str, object]]:
    dependencies = value.get("dependencies") if isinstance(value, dict) else None
    fixes = value.get("fixes") if isinstance(value, dict) else None
    if not isinstance(dependencies, list) or not isinstance(fixes, list):
        _invalid("pip-audit report must contain dependencies and fixes lists")
    findings: list[dict[str, object]] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            _invalid("pip-audit dependency entry is invalid")
        name = dependency.get("name")
        version = dependency.get("version")
        vulnerabilities = dependency.get("vulns")
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(vulnerabilities, list):
            _invalid("pip-audit dependency details are invalid")
        findings.extend(
            _pip_advisory_finding(name, version, requirements, advisory)
            for advisory in vulnerabilities
        )
    return findings


def _scan(
    scan_id: str,
    scanner_name: str,
    scanner_version: str,
    scanner_format: str,
    scope: str,
    report_path: Path,
    exit_code: int,
    input_path: Path,
    input_display_path: str,
    parse: Callable[[object], list[dict[str, object]]],
) -> dict[str, object]:
    if exit_code not in {0, 1}:
        _invalid(f"{scan_id} scanner exit code must be zero or one")
    if not scanner_version.strip():
        _invalid(f"{scan_id} scanner version is required")
    findings = parse(_read_json(report_path))
    if (exit_code == 0) != (not findings):
        _invalid(f"{scan_id} scanner exit code does not match findings")
    return {
        "id": scan_id,
        "scanner": {"name": scanner_name, "version": scanner_version, "format": scanner_format},
        "scope": scope,
        "exit_code": exit_code,
        "input": {"path": input_display_path, "sha256": _sha256(input_path)},
        "summary": _summary(findings),
        "findings": findings,
    }


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build sanitized dependency advisory evidence.")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--npm-version", required=True)
    parser.add_argument("--pip-audit-version", required=True)
    for scanner in ("npm-full", "npm-runtime", "pip-runtime", "pip-development"):
        parser.add_argument(f"--{scanner}-report", type=Path, required=True)
        parser.add_argument(f"--{scanner}-exit-code", type=int, required=True)
    parser.add_argument("--frontend-lock", type=Path, required=True)
    parser.add_argument("--backend-requirements", type=Path, required=True)
    parser.add_argument("--backend-dev-requirements", type=Path, required=True)
    parser.add_argument("--exception-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def build(args: argparse.Namespace) -> dict[str, object]:
    frontend_lock = args.frontend_lock
    backend_requirements = args.backend_requirements
    backend_dev_requirements = args.backend_dev_requirements
    scans = [
        _scan("frontend_full", "npm-audit", args.npm_version, "npm-audit-v2-json", "build_and_runtime", args.npm_full_report, args.npm_full_exit_code, frontend_lock, "frontend/package-lock.json", lambda value: _npm_findings(value, frontend_lock)),
        _scan("frontend_runtime", "npm-audit", args.npm_version, "npm-audit-v2-json", "runtime", args.npm_runtime_report, args.npm_runtime_exit_code, frontend_lock, "frontend/package-lock.json", lambda value: _npm_findings(value, frontend_lock)),
        _scan("backend_runtime", "pip-audit", args.pip_audit_version, "pip-audit-2-json", "runtime", args.pip_runtime_report, args.pip_runtime_exit_code, backend_requirements, "backend/requirements.txt", lambda value: _pip_findings(value, _read_requirements(backend_requirements))),
        _scan("backend_development", "pip-audit", args.pip_audit_version, "pip-audit-2-json", "development", args.pip_development_report, args.pip_development_exit_code, backend_dev_requirements, "backend/requirements-dev.txt", lambda value: _pip_findings(value, _read_requirements(backend_dev_requirements))),
    ]
    scans_by_id = {str(scan["id"]): scan for scan in scans}
    policy = load_policy(args.exception_policy)
    accepted = accepted_development_findings(scans_by_id, policy, frontend_lock.resolve().parents[1])
    accepted_keys = {
        (item["scan_id"], json.dumps(item["finding"], sort_keys=True, separators=(",", ":")))
        for item in accepted
    }
    unresolved = [
        finding
        for scan in scans
        for finding in scan["findings"]
        if (scan["id"], json.dumps(finding, sort_keys=True, separators=(",", ":"))) not in accepted_keys
    ]
    return {
        "schema_version": 1,
        "candidate": {"commit_sha": args.commit_sha},
        "status": "PASSED" if not unresolved else "FAILED",
        "scans": scans,
        "policy": policy_evidence(args.exception_policy),
        "accepted_development_findings": accepted,
    }


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    evidence = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return 0 if evidence["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

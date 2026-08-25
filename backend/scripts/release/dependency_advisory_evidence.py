"""Build sanitized dependency-advisory evidence from npm and pip scanner JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from scripts.release.advisory_policy import (
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


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"scanner report could not be read: {path}") from exc


def _read_requirements(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = REQUIREMENT_NAME.match(stripped)
        if not match:
            raise ValueError(f"unsupported requirement syntax in {path.name}")
        names.add(match.group(1).replace("_", "-").casefold())
    return names


def _summary(findings: list[dict[str, object]]) -> dict[str, int]:
    totals = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        severity = finding["severity"]
        assert isinstance(severity, str)
        totals[severity] += 1
    totals["total"] = len(findings)
    return totals


def _npm_findings(value: object, lock_path: Path) -> list[dict[str, object]]:
    if not isinstance(value, dict) or value.get("auditReportVersion") != 2:
        raise ValueError("npm report must use audit report version 2")
    vulnerabilities = value.get("vulnerabilities")
    metadata = value.get("metadata")
    metadata_vulnerabilities = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(vulnerabilities, dict) or not isinstance(metadata_vulnerabilities, dict):
        raise ValueError("npm report has no vulnerabilities object")
    lock = _read_json(lock_path)
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise ValueError("frontend lock has no packages object")
    findings: list[dict[str, object]] = []
    package_severities = {severity: 0 for severity in NPM_SEVERITIES}
    for package, details in vulnerabilities.items():
        if not isinstance(package, str) or not isinstance(details, dict):
            raise ValueError("npm vulnerability entry is invalid")
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
            raise ValueError("npm vulnerability details are invalid")
        package_severities[package_severity] += 1
        paths = [node for node in nodes if isinstance(node, str)]
        if len(paths) != len(nodes):
            raise ValueError("npm vulnerability paths are invalid")
        versions = sorted(
            {
                entry["version"]
                for path in paths
                for entry in [packages.get(path)]
                if isinstance(entry, dict) and isinstance(entry.get("version"), str)
            }
        )
        fix = details.get("fixAvailable")
        fixed_versions = [fix["version"]] if isinstance(fix, dict) and isinstance(fix.get("version"), str) else []
        for advisory in via:
            if not isinstance(advisory, dict):
                continue
            severity = advisory.get("severity")
            url = advisory.get("url")
            if not isinstance(severity, str) or severity not in SEVERITIES or not isinstance(url, str):
                raise ValueError("npm advisory details are invalid")
            identifier = ADVISORY_ID.search(url)
            if identifier is None:
                raise ValueError("npm advisory has no recognized identifier")
            findings.append(
                {
                    "advisory_id": identifier.group(0),
                    "package": package,
                    "installed_versions": versions,
                    "severity": severity,
                    "direct": direct,
                    "paths": paths,
                    "fixed_versions": fixed_versions,
                }
            )
    expected_metadata = {**package_severities, "total": len(vulnerabilities)}
    if any(metadata_vulnerabilities.get(key) != count for key, count in expected_metadata.items()):
        raise ValueError("npm report metadata does not match vulnerabilities")
    return findings


def _pip_findings(value: object, requirements: set[str]) -> list[dict[str, object]]:
    dependencies = value.get("dependencies") if isinstance(value, dict) else None
    fixes = value.get("fixes") if isinstance(value, dict) else None
    if not isinstance(dependencies, list) or not isinstance(fixes, list):
        raise ValueError("pip-audit report must contain dependencies and fixes lists")
    findings: list[dict[str, object]] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise ValueError("pip-audit dependency entry is invalid")
        name = dependency.get("name")
        version = dependency.get("version")
        vulnerabilities = dependency.get("vulns")
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(vulnerabilities, list):
            raise ValueError("pip-audit dependency details are invalid")
        for advisory in vulnerabilities:
            if not isinstance(advisory, dict):
                raise ValueError("pip-audit advisory entry is invalid")
            identifier = advisory.get("id")
            fixes = advisory.get("fix_versions")
            if not isinstance(identifier, str) or not identifier or not isinstance(fixes, list):
                raise ValueError("pip-audit advisory details are invalid")
            fixed_versions = [item for item in fixes if isinstance(item, str)]
            if len(fixed_versions) != len(fixes):
                raise ValueError("pip-audit fix versions are invalid")
            findings.append(
                {
                    "advisory_id": identifier,
                    "package": name,
                    "installed_versions": [version],
                    "severity": "unknown",
                    "direct": name.replace("_", "-").casefold() in requirements,
                    "paths": [],
                    "fixed_versions": fixed_versions,
                }
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
        raise ValueError(f"{scan_id} scanner exit code must be zero or one")
    if not scanner_version.strip():
        raise ValueError(f"{scan_id} scanner version is required")
    findings = parse(_read_json(report_path))
    if (exit_code == 0) != (not findings):
        raise ValueError(f"{scan_id} scanner exit code does not match findings")
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

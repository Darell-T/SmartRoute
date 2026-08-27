"""Emit bounded, sanitized release-validation evidence for one candidate SHA.

This command deliberately does not deploy, migrate, or roll back SmartRoute.
Those operations belong to the hosting platform.  When operators opt into staging
checks, they must provide the corresponding external evidence rather than let
this local command infer unavailable deployment facts.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass

from scripts.release.advisories import advisory_evidence
from scripts.release.browser import browser_evidence
from scripts.release.provider_fault_validation import (
    run_provider_fault_jitter_validation,
)
from scripts.release.transport import (
    ExternalCheckResult,
    chat_check,
    deployment_evidence,
    endpoint_check,
    offline_check_specs,
    parse_chat_headers,
    readiness_check,
    report_status,
    rollback_evidence,
    validate_staging_url,
)

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
VALID_STATUSES = {STATUS_PASSED, STATUS_FAILED, STATUS_BLOCKED, STATUS_NOT_APPLICABLE}
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
MAX_REQUEST_TIMEOUT_SECONDS = 15.0
MAX_CHAT_BYTES = 65536
SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|app[_-]?key|api[_-]?key|token|secret|password|cookie)"
    r"(?:\s*[:=]\s*|\s+)(?:bearer\s+)?[^\s&]+"
)
URL_SECRET_PATTERN = re.compile(r"([?&](?:token|key|secret|signature)=[^&\s]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Budget:
    max_requests: int
    timeout_seconds: float
    concurrency: int
    max_estimated_cost_usd: float
    estimated_cost_per_request_usd: float

    def _request_budget(self, requested_requests: int) -> str | None:
        if self.max_requests < 0 or requested_requests < 0:
            return "request budgets must be non-negative"
        if requested_requests > self.max_requests:
            return (
                f"requested {requested_requests} requests exceeds max_requests "
                f"of {self.max_requests}"
            )
        if not 0 < self.timeout_seconds <= MAX_REQUEST_TIMEOUT_SECONDS:
            return f"timeout_seconds must be between zero and {MAX_REQUEST_TIMEOUT_SECONDS:g}"
        if self.concurrency < 1:
            return "concurrency must be at least one"
        return None

    def _cost_budget(self, model_chat_enabled: bool) -> str | None:
        if self.max_estimated_cost_usd < 0 or self.estimated_cost_per_request_usd < 0:
            return "cost budgets must be non-negative"
        if model_chat_enabled and (
            self.max_estimated_cost_usd <= 0 or self.estimated_cost_per_request_usd <= 0
        ):
            return "model_chat_smoke requires nonzero cost budget and estimated cost"
        estimated_cost = self.estimated_cost_per_request_usd if model_chat_enabled else 0.0
        if estimated_cost > self.max_estimated_cost_usd:
            return (
                f"estimated cost {estimated_cost:.6f} exceeds max_estimated_cost_usd "
                f"of {self.max_estimated_cost_usd:.6f}"
            )
        return None

    def validate(self, requested_requests: int, model_chat_enabled: bool) -> str | None:
        return self._request_budget(requested_requests) or self._cost_budget(model_chat_enabled)


def redact(value: object) -> str:
    """Remove credentials and query-string secrets from evidence and failures."""
    text = str(value)
    text = URL_SECRET_PATTERN.sub("?redacted", text)
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def check(name: str, status: str, reason: str, **evidence: object) -> dict[str, object]:
    if status not in VALID_STATUSES:
        unsupported = f"unsupported release-validation status: {status}"
        raise ValueError(unsupported)
    return {
        "name": name,
        "status": status,
        "reason": redact(reason),
        "evidence": {key: redact(value) for key, value in evidence.items()},
    }


def requested_request_count(args: argparse.Namespace) -> int:
    if not args.staging:
        return 0
    return (
        2
        + int(args.model_chat_smoke)
        + args.load_requests
        + args.spike_requests
        + args.soak_requests
    )


def result_check(name: str, result: ExternalCheckResult) -> dict[str, object]:
    return check(name, result.status, result.reason, **result.evidence)


def browser_accessibility_check(commit_sha: str, evidence_path: str | None) -> dict[str, object]:
    return result_check("browser_accessibility", browser_evidence(evidence_path, commit_sha))


def offline_checks(commit_sha: str, evidence_path: str | None) -> list[dict[str, object]]:
    checks = [check(name, status, reason) for name, status, reason in offline_check_specs()]
    checks.append(browser_accessibility_check(commit_sha, evidence_path))
    checks.append(provider_fault_jitter_check())
    return checks


def provider_fault_jitter_check() -> dict[str, object]:
    status, reason, evidence = run_provider_fault_jitter_validation()
    return check("provider_fault_jitter", status, reason, **evidence)


def report(
    commit_sha: str,
    checks: list[dict[str, object]],
    requested_requests: int,
    budget: Budget,
    model_chat_enabled: bool,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate": {"commit_sha": redact(commit_sha)},
        "status": report_status(checks),
        "budget": {
            "requested_requests": requested_requests,
            "max_requests": budget.max_requests,
            "timeout_seconds": budget.timeout_seconds,
            "concurrency": budget.concurrency,
            "max_estimated_cost_usd": budget.max_estimated_cost_usd,
            "estimated_model_chat_cost_usd": budget.estimated_cost_per_request_usd if model_chat_enabled else 0.0,
        },
        "checks": checks,
    }


def _mode_error(
    args: argparse.Namespace, commit_sha: str, budget: Budget, requests: int
) -> tuple[str | None, Budget, int, str | None] | None:
    if args.self_test == args.staging:
        return None, budget, requests, "choose exactly one mode: --self-test or --staging"
    if not SHA_PATTERN.fullmatch(commit_sha):
        return None, budget, requests, "commit_sha must be a 7-64 character hexadecimal Git SHA"
    if args.self_test and args.model_chat_smoke:
        return commit_sha, budget, requests, "model_chat_smoke is available only in --staging mode"
    return None


def _sample_error(args: argparse.Namespace, budget: Budget) -> str | None:
    if any(value < 0 for value in (args.load_requests, args.spike_requests, args.soak_requests)):
        return "load, spike, and soak request counts must be non-negative"
    if not 0 < args.max_chat_bytes <= MAX_CHAT_BYTES:
        return f"max_chat_bytes must be between one and {MAX_CHAT_BYTES}"
    if args.soak_interval_seconds < 0 or args.max_soak_seconds <= 0:
        return "soak interval must be non-negative and max soak seconds must be greater than zero"
    if args.max_soak_seconds > 60:
        return "max_soak_seconds may not exceed 60"
    soak_total_seconds = (
        args.soak_requests * budget.timeout_seconds
        + (args.soak_requests - 1) * args.soak_interval_seconds
    )
    if soak_total_seconds > args.max_soak_seconds:
        return "planned soak sample exceeds max_soak_seconds"
    return None


def _staging_arg_error(args: argparse.Namespace) -> str | None:
    if not args.staging:
        return None
    if not args.staging_url:
        return "staging_url is required with --staging"
    return validate_staging_url(args.staging_url)


def validate_args(args: argparse.Namespace) -> tuple[str | None, Budget, int, str | None]:
    commit_sha = args.commit_sha.strip()
    budget = Budget(
        max_requests=args.max_requests,
        timeout_seconds=args.timeout_seconds,
        concurrency=args.concurrency,
        max_estimated_cost_usd=args.max_estimated_cost_usd,
        estimated_cost_per_request_usd=args.estimated_cost_per_request_usd,
    )
    requests = requested_request_count(args)
    mode_error = _mode_error(args, commit_sha, budget, requests)
    if mode_error is not None:
        return mode_error
    error = (
        _sample_error(args, budget)
        or budget.validate(requests, args.model_chat_smoke)
        or _staging_arg_error(args)
    )
    if error:
        return commit_sha, budget, requests, error
    if args.model_chat_smoke:
        try:
            parse_chat_headers(args.chat_header)
        except ValueError as exc:
            return commit_sha, budget, requests, str(exc)
    return commit_sha, budget, requests, None


def _blocked_network_checks(model_chat_enabled: bool) -> list[dict[str, object]]:
    checks = [
        check(
            name,
            STATUS_BLOCKED,
            "valid deployment and rollback evidence are required before staging network checks",
        )
        for name in (
            "liveness",
            "readiness",
            "load_readiness_sample",
            "spike_readiness_sample",
            "soak_readiness_sample",
        )
    ]
    chat_status = STATUS_BLOCKED if model_chat_enabled else STATUS_NOT_APPLICABLE
    chat_reason = (
        "model chat smoke was not requested"
        if not model_chat_enabled
        else "release evidence gate is blocked"
    )
    checks.append(check("chat_smoke", chat_status, chat_reason))
    return checks


def _staging_network_checks(
    args: argparse.Namespace,
    budget: Budget,
    chat_headers: dict[str, str],
) -> list[dict[str, object]]:
    checks = [
        result_check(
            "liveness",
            endpoint_check(args.staging_url, "/health", "GET", budget.timeout_seconds, {}),
        ),
        result_check(
            "readiness",
            endpoint_check(args.staging_url, "/ready", "GET", budget.timeout_seconds, {}),
        ),
    ]
    if args.model_chat_smoke:
        checks.append(
            result_check(
                "chat_smoke",
                chat_check(
                    args.staging_url,
                    args.chat_path,
                    budget.timeout_seconds,
                    chat_headers,
                    args.max_chat_bytes,
                ),
            )
        )
    else:
        checks.append(
            check(
                "chat_smoke",
                STATUS_NOT_APPLICABLE,
                "model chat smoke requires explicit --model-chat-smoke opt-in",
            )
        )
    checks.append(
        result_check(
            "load_readiness_sample",
            readiness_check(
                args.staging_url, budget.timeout_seconds, budget.concurrency, args.load_requests, False
            ),
        )
    )
    checks.append(
        result_check(
            "spike_readiness_sample",
            readiness_check(
                args.staging_url, budget.timeout_seconds, budget.concurrency, args.spike_requests, True
            ),
        )
    )
    checks.append(
        result_check(
            "soak_readiness_sample",
            readiness_check(
                args.staging_url,
                budget.timeout_seconds,
                budget.concurrency,
                args.soak_requests,
                False,
                args.soak_interval_seconds,
            ),
        )
    )
    return checks


def _failed_configuration(
    candidate: str,
    reason: str,
    requests: int,
    budget: Budget,
    model_chat_enabled: bool,
) -> dict[str, object]:
    return report(
        candidate,
        [check("configuration", STATUS_FAILED, reason)],
        requests,
        budget,
        model_chat_enabled,
    )


def _staging_release(
    args: argparse.Namespace,
    commit_sha: str,
    budget: Budget,
    requests: int,
) -> dict[str, object]:
    deployment = deployment_evidence(commit_sha, args.deployment_evidence)
    rollback = rollback_evidence(commit_sha, args.rollback_evidence)
    advisory = advisory_evidence(args.advisory_evidence, commit_sha)
    checks = [
        check(
            "configuration_self_test",
            STATUS_PASSED,
            "budgets validated before network work",
        ),
        check("dependency_advisories", advisory.status, advisory.reason, **advisory.evidence),
        check("deployment_evidence", deployment.status, deployment.reason, **deployment.evidence),
        check("rollback_evidence", rollback.status, rollback.reason, **rollback.evidence),
        check(
            "migration_restore",
            STATUS_NOT_APPLICABLE,
            "repository has no platform deployment, migration, or restore automation",
        ),
        browser_accessibility_check(commit_sha, args.browser_evidence),
        provider_fault_jitter_check(),
    ]
    if deployment.status != STATUS_PASSED or rollback.status != STATUS_PASSED:
        checks.extend(_blocked_network_checks(args.model_chat_smoke))
        return report(commit_sha, checks, requests, budget, args.model_chat_smoke)
    chat_headers: dict[str, str] = {}
    if args.model_chat_smoke:
        try:
            chat_headers = parse_chat_headers(args.chat_header)
        except ValueError as exc:
            checks.extend(
                check(name, STATUS_BLOCKED, str(exc))
                for name in (
                    "chat_smoke",
                    "liveness",
                    "readiness",
                    "load_readiness_sample",
                    "spike_readiness_sample",
                    "soak_readiness_sample",
                )
            )
            return report(commit_sha, checks, requests, budget, True)
    checks.extend(_staging_network_checks(args, budget, chat_headers))
    return report(commit_sha, checks, requests, budget, args.model_chat_smoke)


def run(args: argparse.Namespace) -> dict[str, object]:
    commit_sha, budget, requests, validation_error = validate_args(args)
    candidate = commit_sha or args.commit_sha.strip() or "missing"
    if validation_error:
        return _failed_configuration(
            candidate, validation_error, requests, budget, args.model_chat_smoke
        )
    if commit_sha is None:
        return _failed_configuration(
            candidate, "commit_sha is required", requests, budget, args.model_chat_smoke
        )
    if args.self_test:
        return report(
            commit_sha,
            offline_checks(commit_sha, args.browser_evidence),
            requests,
            budget,
            False,
        )
    return _staging_release(args, commit_sha, budget, requests)


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Produce sanitized release-validation evidence.")
    parser.add_argument("--commit-sha", default="", help="Immutable candidate Git SHA (required).")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Explicitly request offline deterministic validation.",
    )
    parser.add_argument("--staging", action="store_true", help="Opt into bounded staging network checks.")
    parser.add_argument("--staging-url", default="", help="Base staging URL; never emitted in output.")
    parser.add_argument("--chat-path", default="/api/agent/chat")
    parser.add_argument(
        "--model-chat-smoke",
        action="store_true",
        help="Opt into one paid model-backed chat SSE smoke.",
    )
    parser.add_argument(
        "--chat-header",
        action="append",
        default=[],
        help="X-App-Key header as 'X-App-Key: value'; output redacts values.",
    )
    parser.add_argument("--max-chat-bytes", type=int, default=65536)
    parser.add_argument(
        "--advisory-evidence",
        help="Sanitized npm/pip advisory evidence bound to the candidate and dependency inputs.",
    )
    parser.add_argument("--deployment-evidence", help="External JSON with commit_sha and instance_ids.")
    parser.add_argument("--rollback-evidence", help="External JSON proving prior SHA restoration.")
    parser.add_argument("--browser-evidence", help="Sanitized Playwright browser evidence bound to the candidate SHA.")
    parser.add_argument("--max-requests", type=int, default=16)
    parser.add_argument("--load-requests", type=int, default=1)
    parser.add_argument("--spike-requests", type=int, default=1)
    parser.add_argument("--soak-requests", type=int, default=1)
    parser.add_argument("--soak-interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-soak-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-estimated-cost-usd", type=float, default=0.0)
    parser.add_argument("--estimated-cost-per-request-usd", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    result = run(arguments(argv))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {STATUS_PASSED, STATUS_NOT_APPLICABLE} else 1


if __name__ == "__main__":
    raise SystemExit(main())

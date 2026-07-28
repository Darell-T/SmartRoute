"""Bounded HTTP and SSE primitives for release validation.

The command-facing module owns report policy. This module owns transport only,
and deliberately returns generic diagnostics so response content and endpoint
credentials never become release evidence.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class TransportResult:
    passed: bool
    status_code: int | None
    reason: str


@dataclass(frozen=True)
class ExternalCheckResult:
    status: str
    reason: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class ReadinessSampleResult:
    passed: bool
    requested: int
    successful: int
    concurrency: int
    interval_seconds: float
    elapsed_ms: int


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


def parse_chat_headers(values: Iterable[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, header_value = value.partition(":")
        if not separator or not name.strip() or not header_value.strip():
            raise ValueError("chat headers must use 'Name: value' syntax")
        headers[name.strip()] = header_value.strip()
    if "x-app-key" not in {name.casefold() for name in headers}:
        raise ValueError("model chat smoke requires the SmartRoute X-App-Key header")
    return headers


def validate_staging_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "staging_url must be an absolute http or https URL"
    if parsed.username is not None or parsed.password is not None:
        return "staging_url must not contain embedded credentials"
    return None


def offline_check_specs() -> list[tuple[str, str, str]]:
    return [
        ("configuration_self_test", "PASSED", "offline budget and schema checks completed"),
        ("dependency_advisories", "NOT_APPLICABLE", "staging checks were not requested"),
        ("liveness", "NOT_APPLICABLE", "staging checks were not requested"),
        ("readiness", "NOT_APPLICABLE", "staging checks were not requested"),
        ("chat_smoke", "NOT_APPLICABLE", "model chat smoke was not requested"),
        ("load_readiness_sample", "NOT_APPLICABLE", "staging checks were not requested"),
        ("spike_readiness_sample", "NOT_APPLICABLE", "staging checks were not requested"),
        ("soak_readiness_sample", "NOT_APPLICABLE", "staging checks were not requested"),
        ("deployment_evidence", "NOT_APPLICABLE", "staging checks were not requested"),
        ("rollback_evidence", "NOT_APPLICABLE", "staging checks were not requested"),
        ("migration_restore", "NOT_APPLICABLE", "repository has no migration or restore automation"),
        ("browser_accessibility", "BLOCKED", "pinned browser and accessibility dependencies are not approved"),
    ]


def report_status(checks: Iterable[dict[str, object]]) -> str:
    statuses = {str(item["status"]) for item in checks}
    if "FAILED" in statuses:
        return "FAILED"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "PASSED" in statuses:
        return "PASSED"
    return "NOT_APPLICABLE"


class SseTerminalParser:
    """Parse the small event subset needed to prove a successful chat turn."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._received_bytes = 0
        self._buffer = b""
        self._event_name = ""
        self._data_lines: list[str] = []

    def feed(self, chunk: bytes) -> TransportResult | None:
        self._received_bytes += len(chunk)
        if self._received_bytes > self._max_bytes:
            return TransportResult(False, None, "SSE stream exceeded byte limit")
        self._buffer += chunk
        while b"\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split(b"\n", 1)
            result = self._line(raw_line.rstrip(b"\r"))
            if result is not None:
                return result
        return None

    def finish(self) -> TransportResult:
        return TransportResult(False, None, "SSE stream ended without a terminal event")

    def _line(self, raw_line: bytes) -> TransportResult | None:
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            return TransportResult(False, None, "SSE stream contained invalid UTF-8")

        if not line:
            return self._finish_event()
        if line.startswith(":"):
            return None
        if line.startswith("event:"):
            if self._event_name:
                return TransportResult(False, None, "SSE event was malformed")
            self._event_name = line.removeprefix("event:").strip()
            if not self._event_name:
                return TransportResult(False, None, "SSE event was malformed")
            return None
        if line.startswith("data:"):
            self._data_lines.append(line.removeprefix("data:").strip())
            return None
        return TransportResult(False, None, "SSE stream contained an unsupported frame")

    def _finish_event(self) -> TransportResult | None:
        if not self._event_name:
            if self._data_lines:
                return TransportResult(False, None, "SSE event was malformed")
            return None
        if len(self._data_lines) != 1:
            return TransportResult(False, None, "SSE event was malformed")

        try:
            payload = json.loads(self._data_lines[0])
        except json.JSONDecodeError:
            return TransportResult(False, None, "SSE event payload was malformed")

        event_name = self._event_name
        self._event_name = ""
        self._data_lines = []
        if event_name in {"error", "stream_error"}:
            return TransportResult(False, None, "SSE stream emitted an error event")
        if event_name != "done":
            return None
        if not isinstance(payload, dict) or payload.get("stop_reason") not in {
            "end_turn",
            "clarification_required",
        }:
            return TransportResult(
                False,
                None,
                "SSE stream ended without a successful terminal signal",
            )
        return TransportResult(True, None, "successful SSE terminal event received")


def http_check(
    url: str,
    method: str,
    timeout_seconds: float,
    headers: dict[str, str],
    body: bytes | None = None,
) -> TransportResult:
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, content=body)
    except httpx.TimeoutException:
        return TransportResult(False, None, "request timed out")
    except httpx.HTTPError:
        return TransportResult(False, None, "request failed")
    return TransportResult(
        response.is_success,
        response.status_code,
        "response received" if response.is_success else "non-success response",
    )


def readiness_sample(
    base_url: str,
    timeout_seconds: float,
    concurrency: int,
    count: int,
    is_concurrent: bool,
    interval_seconds: float = 0.0,
) -> ReadinessSampleResult:
    started = time.monotonic()
    url = base_url.rstrip("/") + "/ready"
    if is_concurrent:
        workers = min(concurrency, count)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(http_check, url, "GET", timeout_seconds, {})
                for _ in range(count)
            ]
            results = [future.result() for future in futures]
    else:
        workers = 1
        results = []
        for index in range(count):
            if index:
                time.sleep(interval_seconds)
            results.append(http_check(url, "GET", timeout_seconds, {}))
    successes = sum(1 for result in results if result.passed)
    return ReadinessSampleResult(
        passed=successes == count,
        requested=count,
        successful=successes,
        concurrency=workers,
        interval_seconds=interval_seconds,
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )


def endpoint_check(
    base_url: str,
    path: str,
    method: str,
    timeout_seconds: float,
    headers: dict[str, str],
    body: bytes | None = None,
) -> ExternalCheckResult:
    result = http_check(base_url.rstrip("/") + path, method, timeout_seconds, headers, body)
    return ExternalCheckResult(
        "PASSED" if result.passed else "FAILED",
        result.reason,
        {"http_status": result.status_code if result.status_code is not None else "none"},
    )


def chat_check(
    base_url: str,
    path: str,
    timeout_seconds: float,
    headers: dict[str, str],
    max_bytes: int,
) -> ExternalCheckResult:
    body = json.dumps({"message": "Release validation chat smoke."}).encode("utf-8")
    result = chat_sse_check(
        base_url.rstrip("/") + path,
        timeout_seconds,
        {"Content-Type": "application/json", **headers},
        body,
        max_bytes,
    )
    return ExternalCheckResult(
        "PASSED" if result.passed else "FAILED",
        result.reason,
        {"http_status": result.status_code if result.status_code is not None else "none"},
    )


def readiness_check(
    base_url: str,
    timeout_seconds: float,
    concurrency: int,
    count: int,
    is_concurrent: bool,
    interval_seconds: float = 0.0,
) -> ExternalCheckResult:
    if count == 0:
        return ExternalCheckResult("NOT_APPLICABLE", "request count is zero", {})
    result = readiness_sample(
        base_url,
        timeout_seconds,
        concurrency,
        count,
        is_concurrent,
        interval_seconds,
    )
    return ExternalCheckResult(
        "PASSED" if result.passed else "FAILED",
        "bounded readiness sample succeeded" if result.passed else "bounded readiness sample failed",
        {
            "requested": result.requested,
            "successful": result.successful,
            "concurrency": result.concurrency,
            "interval_seconds": result.interval_seconds,
            "elapsed_ms": result.elapsed_ms,
        },
    )


def run_advisory_commands(
    commands: list[str],
    timeout_seconds: float,
) -> ExternalCheckResult:
    if not commands:
        return ExternalCheckResult("BLOCKED", "no advisory scanner command was supplied", {})
    results: list[dict[str, object]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                shlex.split(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return ExternalCheckResult("FAILED", f"scanner execution failed: {exc}", {})
        results.append({"command": command, "exit_code": completed.returncode})
    if any(result["exit_code"] != 0 for result in results):
        return ExternalCheckResult("FAILED", "one or more scanner commands failed", {"commands": results})
    return ExternalCheckResult("PASSED", "all supplied scanner commands exited zero", {"commands": results})


def deployment_evidence(
    commit_sha: str,
    path_value: str | None,
) -> ExternalCheckResult:
    evidence, error = _read_evidence(path_value, "deployment evidence")
    if error:
        return ExternalCheckResult("BLOCKED", error, {})
    assert evidence is not None
    instances = evidence.get("instance_ids")
    if evidence.get("commit_sha") != commit_sha:
        return ExternalCheckResult("FAILED", "deployment evidence SHA does not match candidate", {})
    if not isinstance(instances, list) or not all(isinstance(item, str) and item for item in instances):
        return ExternalCheckResult("BLOCKED", "deployment evidence has no instance_ids", {})
    return ExternalCheckResult(
        "PASSED",
        "external deployment evidence matches candidate",
        {"instance_count": len(instances)},
    )


def rollback_evidence(
    commit_sha: str,
    path_value: str | None,
) -> ExternalCheckResult:
    evidence, error = _read_evidence(path_value, "rollback evidence")
    if error:
        return ExternalCheckResult("BLOCKED", error, {})
    assert evidence is not None
    previous_sha = evidence.get("previous_commit_sha")
    restored_sha = evidence.get("restored_commit_sha")
    if not isinstance(previous_sha, str) or not SHA_PATTERN.fullmatch(previous_sha):
        return ExternalCheckResult("BLOCKED", "rollback evidence has no valid previous_commit_sha", {})
    if restored_sha != previous_sha or evidence.get("result") != "passed":
        return ExternalCheckResult("FAILED", "rollback evidence does not prove restoration", {})
    if previous_sha == commit_sha:
        return ExternalCheckResult("FAILED", "rollback target cannot equal candidate SHA", {})
    return ExternalCheckResult("PASSED", "external rollback evidence was supplied", {})


def _read_evidence(
    path_value: str | None,
    label: str,
) -> tuple[dict[str, object] | None, str | None]:
    if not path_value:
        return None, f"{label} was not supplied"
    path = Path(path_value)
    if not path.is_file():
        return None, f"{label} file does not exist"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, f"{label} is unreadable"
    if not isinstance(loaded, dict):
        return None, f"{label} must contain a JSON object"
    return loaded, None


async def _read_chat_sse(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str],
    body: bytes,
    max_bytes: int,
) -> TransportResult:
    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("POST", url, headers=headers, content=body) as response:
                if not response.is_success:
                    return TransportResult(False, response.status_code, "non-success response")
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type.casefold():
                    return TransportResult(False, response.status_code, "chat response was not an SSE stream")
                parser = SseTerminalParser(max_bytes)
                async for chunk in response.aiter_raw():
                    result = parser.feed(chunk)
                    if result is not None:
                        return TransportResult(result.passed, response.status_code, result.reason)
                return parser.finish()
    except httpx.HTTPError:
        return TransportResult(False, None, "chat request failed")


async def _run_with_deadline(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str],
    body: bytes,
    max_bytes: int,
) -> TransportResult:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _read_chat_sse(url, timeout_seconds, headers, body, max_bytes)
    except TimeoutError:
        return TransportResult(False, None, "SSE stream exceeded total time limit")


def chat_sse_check(
    url: str,
    timeout_seconds: float,
    headers: dict[str, str],
    body: bytes,
    max_bytes: int,
) -> TransportResult:
    """Run the async stream under a cancellable total-operation deadline."""
    return asyncio.run(_run_with_deadline(url, timeout_seconds, headers, body, max_bytes))

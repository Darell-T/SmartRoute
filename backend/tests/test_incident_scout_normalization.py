"""Focused tests for pure scout parsing and evidence normalization."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import pytest
from app.services.incidents.normalization import incident_id_for
from app.services.incidents.scout_normalization import (
    build_incident_inputs,
    claim_ref_for,
    is_valid_web_payload,
    is_valid_x_payload,
    normalize_web_corroborations,
    normalize_x_claims,
    observed_at_iso,
    per_post_source_id,
)
from app.services.trips.crowds.search_normalization import parse_json

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
X_URL = "https://x.com/nycdesk/status/1234567890"
X_URL_2 = "https://x.com/nycdesk/status/9999999999"
WEB_URL = "https://news.example.test/report"
WEB_URL_2 = "https://other-news.example.test/report"

EXPECTED_KEYS = {
    "state", "source", "source_id", "location_name", "description", "severity",
    "impact_scope", "observed_at", "expires_at", "source_coverage",
    "corroboration_state", "advisor_eligible", "source_records",
    "affected_stop_ids", "affected_route_ids", "affected_corridor_ids",
    "affected_batch_ids",
}


def _claim(*, source_url=X_URL, observed_at=None, **overrides):
    claim = {
        "location": "Lexington Avenue",
        "description": "FDNY on scene at a street-level emergency.",
        "severity": "high",
        "impact_scope": "subway_operations",
        "route_ids": ["6"],
        "source_url": source_url,
        "observed_at": observed_at or (NOW - timedelta(minutes=20)).isoformat(),
    }
    claim.update(overrides)
    return claim


class ParseAndIdentityTests(unittest.TestCase):
    def test_parse_json_strict_object_and_fenced(self):
        assert parse_json('{"incidents": []}') == {"incidents": []}
        assert parse_json('```json\n{"incidents": []}\n```') == {"incidents": []}
        assert parse_json("not json") is None
        assert parse_json("[1, 2]") is None
        assert parse_json("") is None

    def test_per_post_identity_distinguishes_posts_from_one_account(self):
        first = per_post_source_id(X_URL)
        second = per_post_source_id(X_URL_2)
        assert first != second
        assert first == per_post_source_id(X_URL)
        assert first.startswith("x:nycdesk:")
        assert len(first) <= 80
        assert "status/" not in first
        assert per_post_source_id(WEB_URL) == "web:news.example.test"
        assert per_post_source_id("not a url") is None

    def test_per_post_identity_is_bounded_and_deterministic_for_long_paths(self):
        long_url = "https://x.com/nycdesk/status/" + ("1" * 500)
        other_url = "https://x.com/nycdesk/status/" + ("1" * 499)
        first = per_post_source_id(long_url)
        assert first == per_post_source_id(long_url)
        assert len(first) <= 80
        assert "1" * 500 not in first
        assert first != per_post_source_id(other_url)

    def test_claim_ref_is_opaque_and_stable(self):
        assert claim_ref_for("x:a/1") == claim_ref_for("x:a/1")
        assert claim_ref_for("x:a/1") != claim_ref_for("x:a/2")
        assert claim_ref_for("x:a/1").startswith("cr_")

    def test_observed_at_requires_offset_aware_and_fresh(self):
        assert observed_at_iso((NOW - timedelta(hours=2)).isoformat(), now=NOW) is not None
        assert observed_at_iso((NOW - timedelta(hours=2)).isoformat(), now=NOW).endswith("Z")
        assert observed_at_iso((NOW - timedelta(hours=6)).isoformat(), now=NOW) is not None
        assert observed_at_iso((NOW + timedelta(minutes=10)).isoformat(), now=NOW) is not None
        assert observed_at_iso((NOW - timedelta(hours=7)).isoformat(), now=NOW) is None
        assert observed_at_iso((NOW + timedelta(minutes=30)).isoformat(), now=NOW) is None
        assert observed_at_iso("2026-08-02T10:00:00", now=NOW) is None
        assert observed_at_iso("garbage", now=NOW) is None
        assert observed_at_iso("", now=NOW) is None


class PayloadContractTests(unittest.TestCase):
    def test_x_contract_requires_incidents_list(self):
        assert is_valid_x_payload({"incidents": []})
        assert is_valid_x_payload({"incidents": [{}]})
        assert not is_valid_x_payload({})
        assert not is_valid_x_payload({"incidents": None})
        assert not is_valid_x_payload({"incidents": "nope"})
        assert not is_valid_x_payload({"incidents": {"a": 1}})
        assert not is_valid_x_payload({"incidents": 3})
        assert not is_valid_x_payload([])

    def test_web_contract_requires_corroborations_list(self):
        assert is_valid_web_payload({"corroborations": []})
        assert is_valid_web_payload({"corroborations": [{}]})
        assert not is_valid_web_payload({})
        assert not is_valid_web_payload({"corroborations": "nope"})
        assert not is_valid_web_payload({"corroborations": {"a": 1}})
        assert not is_valid_web_payload({"incidents": []})


class XClaimNormalizationTests(unittest.TestCase):
    def test_accepts_cited_fresh_claim_with_bounded_fields(self):
        claims = normalize_x_claims(
            {"incidents": [_claim(route_ids=["6", "6"], stop_ids="A01")]},
            citations=[X_URL],
            now=NOW,
        )
        assert len(claims) == 1
        claim = claims[0]
        assert claim["location"] == "Lexington Avenue"
        assert claim["severity"] == "high"
        assert claim["impact_scope"] == "subway_operations"
        assert claim["route_ids"] == ["6"]
        assert claim["stop_ids"] == ["A01"]
        assert claim["source_url"] == X_URL
        assert claim["source_id"] == per_post_source_id(X_URL)
        assert claim["observed_at"] == "2026-08-02T15:40:00Z"

    def test_two_posts_same_account_stay_distinct_and_duplicate_citation_dedupes(self):
        claims = normalize_x_claims(
            {"incidents": [
                _claim(),
                _claim(source_url=X_URL_2, observed_at=(NOW - timedelta(minutes=5)).isoformat()),
                _claim(observed_at=(NOW - timedelta(minutes=5)).isoformat()),
            ]},
            citations=[X_URL, X_URL_2],
            now=NOW,
        )
        assert len(claims) == 2
        assert claims[0]["source_id"] != claims[1]["source_id"]
        assert claims[0]["claim_ref"] != claims[1]["claim_ref"]

    def test_rejects_stale_naive_future_uncited_mismatched_and_invalid(self):
        payload = {"incidents": [
            _claim(observed_at=(NOW - timedelta(hours=7)).isoformat()),
            _claim(observed_at="2026-08-02T15:00:00"),
            _claim(observed_at=(NOW + timedelta(minutes=20)).isoformat()),
            _claim(source_url="https://x.com/other/status/1"),
            _claim(source_url=WEB_URL),
            _claim(severity="critical"),
            _claim(impact_scope="citywide"),
            _claim(location="   "),
            _claim(description=""),
            "not a mapping",
        ]}
        assert normalize_x_claims(payload, citations=[X_URL, WEB_URL], now=NOW) == []
        assert normalize_x_claims({"incidents": "nope"}, citations=[X_URL], now=NOW) == []
        assert normalize_x_claims({}, citations=[X_URL], now=NOW) == []


class WebCorroborationTests(unittest.TestCase):
    def setUp(self):
        self.claims = {
            claim["claim_ref"]: claim
            for claim in normalize_x_claims(
                {"incidents": [_claim(), _claim(source_url=X_URL_2)]},
                citations=[X_URL, X_URL_2],
                now=NOW,
            )
        }
        self.ref = next(iter(self.claims))

    def _corroborate(self, items, *, citations):
        return normalize_web_corroborations(
            {"corroborations": items},
            claims_by_ref=self.claims,
            citations=citations,
            now=NOW,
        )

    def test_accepts_only_matching_claims_and_exact_web_citations(self):
        result = self._corroborate(
            [
                {"claim_ref": self.ref, "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": "cr_unknown", "source_url": "https://news.example.test/other",
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": "https://news.example.test/uncited",
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": X_URL,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(hours=7)).isoformat()},
            ],
            citations=[WEB_URL, X_URL],
        )
        assert len(result) == 1
        assert result[0]["claim_ref"] == self.ref
        assert result[0]["source_url"] == WEB_URL

    def test_same_domain_counts_once_but_distinct_domain_adds(self):
        repeated = self._corroborate(
            [
                {"claim_ref": self.ref, "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": "https://news.example.test/other-page",
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
            ],
            citations=[WEB_URL, "https://news.example.test/other-page"],
        )
        assert len(repeated) == 1
        distinct = self._corroborate(
            [
                {"claim_ref": self.ref, "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": WEB_URL_2,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
            ],
            citations=[WEB_URL, WEB_URL_2],
        )
        assert len(distinct) == 2
        assert {item["source_id"] for item in distinct} == {"web:news.example.test", "web:other-news.example.test"}


class BuildIncidentInputsTests(unittest.TestCase):
    def test_x_only_incident_unconfirmed_with_bounded_surface(self):
        claims = normalize_x_claims({"incidents": [_claim()]}, citations=[X_URL], now=NOW)
        incidents = build_incident_inputs(claims, [], batch_id="midtown-manhattan", now=NOW)
        assert len(incidents) == 1
        incident = incidents[0]
        assert set(incident) == EXPECTED_KEYS
        assert incident["state"] == "unconfirmed"
        assert incident["source"] == "x_search"
        assert incident["source_id"] == per_post_source_id(X_URL)
        assert incident["corroboration_state"] == "uncorroborated"
        assert not incident["advisor_eligible"]
        assert incident["source_coverage"] == ["x_search"]
        assert incident["affected_batch_ids"] == ["midtown-manhattan"]
        assert incident["source_records"][0]["source"] == "x_search"
        assert incident["expires_at"] == pytest.approx(NOW.timestamp() + 30 * 60, abs=1.0)

    def test_corroborated_non_nearby_eligible_and_nearby_not(self):
        claims = normalize_x_claims(
            {"incidents": [
                _claim(),
                _claim(source_url=X_URL_2, impact_scope="nearby",
                       observed_at=(NOW - timedelta(minutes=5)).isoformat()),
            ]},
            citations=[X_URL, X_URL_2],
            now=NOW,
        )
        corroborations = normalize_web_corroborations(
            {"corroborations": [
                {"claim_ref": claims[0]["claim_ref"], "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=4)).isoformat()},
                {"claim_ref": claims[1]["claim_ref"], "source_url": WEB_URL_2,
                 "observed_at": (NOW - timedelta(minutes=4)).isoformat()},
            ]},
            claims_by_ref={claim["claim_ref"]: claim for claim in claims},
            citations=[WEB_URL, WEB_URL_2],
            now=NOW,
        )
        incidents = build_incident_inputs(
            claims, corroborations, batch_id="midtown-manhattan", now=NOW
        )
        subway, nearby = incidents
        assert subway["state"] == "confirmed"
        assert subway["corroboration_state"] == "corroborated"
        assert subway["advisor_eligible"]
        assert subway["source_coverage"] == ["x_search", "web_search"]
        assert len(subway["source_records"]) == 2
        assert subway["expires_at"] == pytest.approx(NOW.timestamp() + 6 * 3600, abs=1.0)
        assert nearby["state"] == "confirmed"
        assert not nearby["advisor_eligible"]

    def test_corroboration_preserves_incident_identity(self):
        claims = normalize_x_claims(
            {"incidents": [
                _claim(),
                _claim(source_url=X_URL_2, observed_at=(NOW - timedelta(minutes=5)).isoformat()),
            ]},
            citations=[X_URL, X_URL_2],
            now=NOW,
        )
        corroborations = normalize_web_corroborations(
            {"corroborations": [
                {"claim_ref": claims[0]["claim_ref"], "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=4)).isoformat()},
            ]},
            claims_by_ref={claim["claim_ref"]: claim for claim in claims},
            citations=[WEB_URL],
            now=NOW,
        )
        x_only = build_incident_inputs(claims, [], batch_id="midtown-manhattan", now=NOW)
        corroborated = build_incident_inputs(
            claims, corroborations, batch_id="midtown-manhattan", now=NOW
        )
        assert incident_id_for(x_only[0]) == incident_id_for(corroborated[0])
        assert x_only[0]["state"] == "unconfirmed"
        assert corroborated[0]["state"] == "confirmed"
        assert len(x_only[0]["source_records"]) == 1
        assert len(corroborated[0]["source_records"]) == 2
        assert incident_id_for(x_only[0]) != incident_id_for(x_only[1])

    def test_emitted_source_records_are_bounded(self):
        long_url = "https://x.com/nycdesk/status/" + ("2" * 400)
        claims = normalize_x_claims(
            {"incidents": [_claim(source_url=long_url)]}, citations=[long_url], now=NOW
        )
        assert len(claims) == 1
        incidents = build_incident_inputs(claims, [], batch_id="midtown-manhattan", now=NOW)
        record = incidents[0]["source_records"][0]
        assert len(record["source"]) <= 80
        assert len(record["source_id"]) <= 120
        assert len(record["source_url"]) <= 240
        assert "2" * 400 not in record["source_url"]


if __name__ == "__main__":
    unittest.main()

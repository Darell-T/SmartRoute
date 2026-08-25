"""Focused tests for pure scout parsing and evidence normalization."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

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

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
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
        self.assertEqual(parse_json('{"incidents": []}'), {"incidents": []})
        self.assertEqual(parse_json('```json\n{"incidents": []}\n```'), {"incidents": []})
        self.assertIsNone(parse_json("not json"))
        self.assertIsNone(parse_json("[1, 2]"))
        self.assertIsNone(parse_json(""))

    def test_per_post_identity_distinguishes_posts_from_one_account(self):
        first = per_post_source_id(X_URL)
        second = per_post_source_id(X_URL_2)
        self.assertNotEqual(first, second)
        self.assertEqual(first, per_post_source_id(X_URL))
        self.assertTrue(first.startswith("x:nycdesk:"))
        self.assertLessEqual(len(first), 80)
        self.assertNotIn("status/", first)
        self.assertEqual(per_post_source_id(WEB_URL), "web:news.example.test")
        self.assertIsNone(per_post_source_id("not a url"))

    def test_per_post_identity_is_bounded_and_deterministic_for_long_paths(self):
        long_url = "https://x.com/nycdesk/status/" + ("1" * 500)
        other_url = "https://x.com/nycdesk/status/" + ("1" * 499)
        first = per_post_source_id(long_url)
        self.assertEqual(first, per_post_source_id(long_url))
        self.assertLessEqual(len(first), 80)
        self.assertNotIn("1" * 500, first)
        self.assertNotEqual(first, per_post_source_id(other_url))

    def test_claim_ref_is_opaque_and_stable(self):
        self.assertEqual(claim_ref_for("x:a/1"), claim_ref_for("x:a/1"))
        self.assertNotEqual(claim_ref_for("x:a/1"), claim_ref_for("x:a/2"))
        self.assertTrue(claim_ref_for("x:a/1").startswith("cr_"))

    def test_observed_at_requires_offset_aware_and_fresh(self):
        self.assertIsNotNone(observed_at_iso((NOW - timedelta(hours=2)).isoformat(), now=NOW))
        self.assertTrue(observed_at_iso((NOW - timedelta(hours=2)).isoformat(), now=NOW).endswith("Z"))
        self.assertIsNotNone(observed_at_iso((NOW - timedelta(hours=6)).isoformat(), now=NOW))
        self.assertIsNotNone(observed_at_iso((NOW + timedelta(minutes=10)).isoformat(), now=NOW))
        self.assertIsNone(observed_at_iso((NOW - timedelta(hours=7)).isoformat(), now=NOW))
        self.assertIsNone(observed_at_iso((NOW + timedelta(minutes=30)).isoformat(), now=NOW))
        self.assertIsNone(observed_at_iso("2026-08-02T10:00:00", now=NOW))
        self.assertIsNone(observed_at_iso("garbage", now=NOW))
        self.assertIsNone(observed_at_iso("", now=NOW))


class PayloadContractTests(unittest.TestCase):
    def test_x_contract_requires_incidents_list(self):
        self.assertTrue(is_valid_x_payload({"incidents": []}))
        self.assertTrue(is_valid_x_payload({"incidents": [{}]}))
        self.assertFalse(is_valid_x_payload({}))
        self.assertFalse(is_valid_x_payload({"incidents": None}))
        self.assertFalse(is_valid_x_payload({"incidents": "nope"}))
        self.assertFalse(is_valid_x_payload({"incidents": {"a": 1}}))
        self.assertFalse(is_valid_x_payload({"incidents": 3}))
        self.assertFalse(is_valid_x_payload([]))

    def test_web_contract_requires_corroborations_list(self):
        self.assertTrue(is_valid_web_payload({"corroborations": []}))
        self.assertTrue(is_valid_web_payload({"corroborations": [{}]}))
        self.assertFalse(is_valid_web_payload({}))
        self.assertFalse(is_valid_web_payload({"corroborations": "nope"}))
        self.assertFalse(is_valid_web_payload({"corroborations": {"a": 1}}))
        self.assertFalse(is_valid_web_payload({"incidents": []}))


class XClaimNormalizationTests(unittest.TestCase):
    def test_accepts_cited_fresh_claim_with_bounded_fields(self):
        claims = normalize_x_claims(
            {"incidents": [_claim(route_ids=["6", "6"], stop_ids="A01")]},
            citations=[X_URL],
            now=NOW,
        )
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim["location"], "Lexington Avenue")
        self.assertEqual(claim["severity"], "high")
        self.assertEqual(claim["impact_scope"], "subway_operations")
        self.assertEqual(claim["route_ids"], ["6"])
        self.assertEqual(claim["stop_ids"], ["A01"])
        self.assertEqual(claim["source_url"], X_URL)
        self.assertEqual(claim["source_id"], per_post_source_id(X_URL))
        self.assertEqual(claim["observed_at"], "2026-08-02T15:40:00Z")

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
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[0]["source_id"], claims[1]["source_id"])
        self.assertNotEqual(claims[0]["claim_ref"], claims[1]["claim_ref"])

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
        self.assertEqual(
            normalize_x_claims(payload, citations=[X_URL, WEB_URL], now=NOW), []
        )
        self.assertEqual(normalize_x_claims({"incidents": "nope"}, citations=[X_URL], now=NOW), [])
        self.assertEqual(normalize_x_claims({}, citations=[X_URL], now=NOW), [])


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
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["claim_ref"], self.ref)
        self.assertEqual(result[0]["source_url"], WEB_URL)

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
        self.assertEqual(len(repeated), 1)
        distinct = self._corroborate(
            [
                {"claim_ref": self.ref, "source_url": WEB_URL,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
                {"claim_ref": self.ref, "source_url": WEB_URL_2,
                 "observed_at": (NOW - timedelta(minutes=5)).isoformat()},
            ],
            citations=[WEB_URL, WEB_URL_2],
        )
        self.assertEqual(len(distinct), 2)
        self.assertEqual(
            {item["source_id"] for item in distinct},
            {"web:news.example.test", "web:other-news.example.test"},
        )


class BuildIncidentInputsTests(unittest.TestCase):
    def test_x_only_incident_unconfirmed_with_bounded_surface(self):
        claims = normalize_x_claims({"incidents": [_claim()]}, citations=[X_URL], now=NOW)
        incidents = build_incident_inputs(claims, [], batch_id="midtown-manhattan", now=NOW)
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(set(incident), EXPECTED_KEYS)
        self.assertEqual(incident["state"], "unconfirmed")
        self.assertEqual(incident["source"], "x_search")
        self.assertEqual(incident["source_id"], per_post_source_id(X_URL))
        self.assertEqual(incident["corroboration_state"], "uncorroborated")
        self.assertFalse(incident["advisor_eligible"])
        self.assertEqual(incident["source_coverage"], ["x_search"])
        self.assertEqual(incident["affected_batch_ids"], ["midtown-manhattan"])
        self.assertEqual(incident["source_records"][0]["source"], "x_search")
        self.assertAlmostEqual(incident["expires_at"], NOW.timestamp() + 30 * 60, delta=1.0)

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
        self.assertEqual(subway["state"], "confirmed")
        self.assertEqual(subway["corroboration_state"], "corroborated")
        self.assertTrue(subway["advisor_eligible"])
        self.assertEqual(subway["source_coverage"], ["x_search", "web_search"])
        self.assertEqual(len(subway["source_records"]), 2)
        self.assertAlmostEqual(subway["expires_at"], NOW.timestamp() + 6 * 3600, delta=1.0)
        self.assertEqual(nearby["state"], "confirmed")
        self.assertFalse(nearby["advisor_eligible"])

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
        self.assertEqual(incident_id_for(x_only[0]), incident_id_for(corroborated[0]))
        self.assertEqual(x_only[0]["state"], "unconfirmed")
        self.assertEqual(corroborated[0]["state"], "confirmed")
        self.assertEqual(len(x_only[0]["source_records"]), 1)
        self.assertEqual(len(corroborated[0]["source_records"]), 2)
        self.assertNotEqual(incident_id_for(x_only[0]), incident_id_for(x_only[1]))

    def test_emitted_source_records_are_bounded(self):
        long_url = "https://x.com/nycdesk/status/" + ("2" * 400)
        claims = normalize_x_claims(
            {"incidents": [_claim(source_url=long_url)]}, citations=[long_url], now=NOW
        )
        self.assertEqual(len(claims), 1)
        incidents = build_incident_inputs(claims, [], batch_id="midtown-manhattan", now=NOW)
        record = incidents[0]["source_records"][0]
        self.assertLessEqual(len(record["source"]), 80)
        self.assertLessEqual(len(record["source_id"]), 120)
        self.assertLessEqual(len(record["source_url"]), 240)
        self.assertNotIn("2" * 400, record["source_url"])


if __name__ == "__main__":
    unittest.main()

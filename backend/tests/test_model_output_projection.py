"""Focused tests for the model-facing provider-identity projection."""

from __future__ import annotations

from unittest import TestCase

from app.services.agent.model.output_projection import project_route_preparation


class ModelOutputProjectionTests(TestCase):
    def test_normalized_branch_ids_survive_variant_reordering(self) -> None:
        payload = {
            "candidate_set_id": "cs_projection",
            "destination_place_ids": ["pl_alpha", "pl_beta"],
            "candidates": [
                self._candidate("cd_beta_fast", "pl_beta", "ChIJ-beta"),
                self._candidate("cd_alpha_fast", "pl_alpha", "ChIJ-alpha"),
                self._candidate("cd_beta_few_transfers", "pl_beta", "ChIJ-beta"),
                self._candidate("cd_alpha_few_transfers", "pl_alpha", "ChIJ-alpha"),
            ],
            "branch_coverage": [
                {"place_id": "pl_alpha", "provider_place_id": "ChIJ-alpha"},
                {"place_id": "pl_beta", "provider_place_id": "ChIJ-beta"},
            ],
            "provider_place_id": "ChIJ-request-endpoint",
        }

        projected = project_route_preparation(
            payload,
            {"destination_place_ids": ["pl_alpha", "pl_beta"]},
        )

        assert isinstance(projected, dict)
        assert [candidate["destination_place_id"] for candidate in projected["candidates"]] == ["pl_beta", "pl_alpha", "pl_beta", "pl_alpha"]
        assert [branch["place_id"] for branch in projected["branch_coverage"]] == ["pl_alpha", "pl_beta"]
        self._assert_no_provider_identity(projected)

    def test_multi_destination_provider_shape_is_not_assigned_by_candidate_index(self) -> None:
        projected = project_route_preparation(
            {
                "destination_place_ids": ["pl_alpha", "pl_beta"],
                "candidates": [
                    {
                        "candidate_id": "cd_provider-shaped",
                        "destination_place_id": "ChIJ-alpha",
                    },
                    {
                        "candidate_id": "cd_beta",
                        "destination_place_id": "pl_beta",
                    },
                ],
            },
            {"destination_place_ids": ["pl_alpha", "pl_beta"]},
        )

        assert isinstance(projected, dict)
        assert "destination_place_id" not in projected["candidates"][0]
        assert projected["candidates"][1]["destination_place_id"] == "pl_beta"
        self._assert_no_provider_identity(projected)

    @staticmethod
    def _candidate(candidate_id: str, place_id: str, provider_id: str) -> dict:
        return {
            "candidate_id": candidate_id,
            "destination_place_id": place_id,
            "provider_place_id": provider_id,
            "digest": {
                "destination_place_id": place_id,
                "provider_place_id": provider_id,
            },
        }

    def _assert_no_provider_identity(self, value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                assert str(key).casefold() not in {"provider_place_id", "provider_place_ids"}
                self._assert_no_provider_identity(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                self._assert_no_provider_identity(nested)
            return
        assert "chij" not in str(value or "").casefold()

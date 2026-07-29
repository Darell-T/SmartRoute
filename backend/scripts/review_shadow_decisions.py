"""Append a privacy-safe human classification for one shadow observation.

Usage:
  python -m scripts.review_shadow_decisions RECORDS.jsonl REVIEWS.jsonl UUID classification
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.services.validation.shadow import JsonlShadowReviewStore, ReviewClassification, ShadowReview


def _observation_exists(records_path: Path, observation_id: str) -> bool:
    try:
        lines = records_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("observation_id") == observation_id:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify an existing sanitized shadow decision record.")
    parser.add_argument("records", type=Path)
    parser.add_argument("reviews", type=Path)
    parser.add_argument("observation_id")
    parser.add_argument("classification", choices=[item.value for item in ReviewClassification])
    args = parser.parse_args(argv)
    if not _observation_exists(args.records, args.observation_id):
        parser.error("observation_id was not found in the supplied records file")
    review = ShadowReview.create(args.observation_id, args.classification)
    JsonlShadowReviewStore(args.reviews).submit_review(review)
    print(json.dumps(review.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

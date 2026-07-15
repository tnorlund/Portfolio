#!/usr/bin/env python3
"""Measure D0 on exact production crops without writing remote state.

This evaluator is intentionally hard-wired to the development table. It
freezes and deduplicates receipt cohorts before scoring, uses the upload
extractor verbatim, and does not expose any persistence method.
"""

# Script path bootstrapping is intentional for repository-local tooling.
# pylint: disable=wrong-import-position,import-error
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import boto3
import numpy as np
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "receipt_chroma"))
sys.path.insert(0, str(_ROOT / "receipt_dynamo"))
sys.path.insert(0, str(_ROOT / "receipt_upload"))
sys.path.insert(0, str(_ROOT / "tools/glyph-studio/py"))

from build_typeface_registry import build_atlas_registry
from glyphstudio.family_cluster import normalize_glyph
from receipt_chroma import score_typeface_sources
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

from receipt_upload.typeface_fingerprint import extract_letter_crops

_DEV_TABLE = "ReceiptsTable-dc5be22"
_PROD_TABLE = "ReceiptsTable-d7ff76a"
_VALIDATION_PATH = (
    _ROOT / "synthesis_loop/rom_font_manifests/validation_results.json"
)
_DEFAULT_OUTPUT = (
    _ROOT
    / "synthesis_loop/rom_font_manifests/typeface_runtime_calibration_v2.json"
)
_DEFAULT_FIXTURES = (
    _ROOT / "receipt_upload/tests/fixtures/typeface_real_crops_v2.json"
)
_COSTCO_EXCLUSIONS = {
    "0324604e-e1f7-4021-b887-7ef7e012c563#1": "font-review receipt",
    "57cb7f2c-7dcc-4974-9ef8-a460232f3b1d#1": (
        "gold/render calibration receipt"
    ),
}
_COSTCO_CASE_IDS = {
    "1bd3e79faa8b",
    "2161fcdc9dfe",
    "5fb40d6d7877",
    "768e9bd87b94",
    "968d590b548d",
    "a773764c331a",
    "adab1f845703",
    "bd7c717a5764",
    "be2e94f9fcdd",
    "e3da977fae79",
    "e43f7ff94dac",
    "fdbb51b146c1",
}
_VONS_ATLAS_EXCLUSIONS = {"00ded398-af6f-4a49-86f7-c79ccb554e48#1"}


@dataclass
class ReceiptCase:
    """One frozen receipt and its exact runtime evidence."""

    key: str
    merchant: str
    true_sources: tuple[str, ...]
    split: str = ""
    crops: list[tuple[str, np.ndarray]] | None = None
    crop_digest: str = ""
    scores: dict[str, float] | None = None

    @property
    def case_id(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:12]


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode(mask: np.ndarray) -> dict[str, Any]:
    bitmap = np.asarray(mask, dtype=bool)
    if bitmap.ndim != 2 or not bitmap.size:
        raise ValueError(
            f"fixture glyph must be a non-empty bitmap, got {bitmap.shape}"
        )
    return {
        "shape": [int(bitmap.shape[0]), int(bitmap.shape[1])],
        "packed_bits": np.packbits(bitmap, bitorder="big").tobytes().hex(),
    }


def _crop_digest(crops: list[tuple[str, np.ndarray]]) -> str:
    members = []
    for char, mask in crops:
        normalized = normalize_glyph(mask)
        digest = hashlib.sha256()
        digest.update(char.encode())
        digest.update(np.packbits(normalized).tobytes())
        members.append(digest.digest())
    return hashlib.sha256(b"".join(sorted(members))).hexdigest()


def _load_image(s3: Any, receipt: Any) -> Image.Image:
    errors = []
    for bucket, key in (
        (receipt.cdn_s3_bucket, receipt.cdn_s3_key),
        (receipt.raw_s3_bucket, receipt.raw_s3_key),
    ):
        if not bucket or not key:
            continue
        try:
            payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            with Image.open(BytesIO(payload)) as image:
                return image.convert("RGB")
        except (BotoCoreError, ClientError, OSError) as exc:
            errors.append(str(exc))
    raise RuntimeError(f"receipt image unavailable: {errors}")


def _populate_case(
    case: ReceiptCase,
    client: DynamoClient,
    s3: Any,
    details_cache: dict[str, Any],
) -> None:
    image_id, receipt_id_text = case.key.split("#")
    receipt_id = int(receipt_id_text)
    details = details_cache.get(image_id)
    if details is None:
        details = client.get_image_details(image_id)
        details_cache[image_id] = details
    receipt = next(
        (
            item
            for item in details.receipts
            if int(item.receipt_id) == receipt_id
        ),
        None,
    )
    letters = [
        item
        for item in details.receipt_letters
        if int(item.receipt_id) == receipt_id
    ]
    if receipt is None or not letters:
        raise RuntimeError("receipt or ReceiptLetters missing")
    image = _load_image(s3, receipt)
    case.crops = extract_letter_crops(image, letters)
    if not case.crops:
        raise RuntimeError("production extractor returned no crops")
    case.crop_digest = _crop_digest(case.crops)


def _validation_cases(validation: dict[str, Any]) -> list[ReceiptCase]:
    cases = []
    for merchant, result in sorted(validation["merchants"].items()):
        true_sources = [f"ROM:{result['rom_winner']}"]
        if merchant == "The Home Depot":
            # The frozen #1110 validation called D1/pixCrog a measured tie;
            # neither source may treat Home Depot as a foreign negative.
            true_sources.append("ROM:pixCrog")
        slug = result.get("shipped_slug")
        if slug:
            true_sources.append(f"MERCHANT:{slug}")
        for key in sorted(result["receipts"]):
            cases.append(
                ReceiptCase(
                    key=key,
                    merchant=merchant,
                    true_sources=tuple(sorted(true_sources)),
                )
            )
    return cases


def _costco_cases(client: DynamoClient) -> tuple[list[ReceiptCase], str]:
    places = []
    cursor = None
    while True:
        page, cursor = client.get_receipt_places_by_merchant(
            "Costco Wholesale", last_evaluated_key=cursor
        )
        places.extend(page)
        if not cursor:
            break
    candidates = sorted(
        {
            f"{place.image_id}#{int(place.receipt_id)}"
            for place in places
            if place.merchant_name == "Costco Wholesale"
        }
        - set(_COSTCO_EXCLUSIONS)
    )
    keys = [
        key
        for key in candidates
        if hashlib.sha256(key.encode()).hexdigest()[:12] in _COSTCO_CASE_IDS
    ]
    selected_ids = {
        hashlib.sha256(key.encode()).hexdigest()[:12] for key in keys
    }
    if len(keys) != len(_COSTCO_CASE_IDS) or selected_ids != _COSTCO_CASE_IDS:
        raise RuntimeError(
            "the privacy-pinned Costco cohort is missing or ambiguous: "
            f"expected {len(_COSTCO_CASE_IDS)}, got {len(keys)}"
        )
    return (
        [
            ReceiptCase(
                key=key,
                merchant="Costco Wholesale",
                true_sources=("MERCHANT:costco",),
            )
            for key in keys
        ],
        _json_sha256(sorted(selected_ids)),
    )


def _dedupe_and_split(
    cases: list[ReceiptCase],
) -> tuple[list[ReceiptCase], list[dict[str, str]]]:
    unique = []
    duplicates = []
    for merchant in sorted({case.merchant for case in cases}):
        seen: dict[str, ReceiptCase] = {}
        merchant_cases = sorted(
            (case for case in cases if case.merchant == merchant),
            key=lambda case: case.key,
        )
        for case in merchant_cases:
            previous = seen.get(case.crop_digest)
            if previous is not None:
                duplicates.append(
                    {
                        "case_id": case.case_id,
                        "duplicates_case_id": previous.case_id,
                    }
                )
                continue
            seen[case.crop_digest] = case
            unique.append(case)
        for index, case in enumerate(seen.values()):
            case.split = "calibration" if index % 2 == 0 else "holdout"
    return unique, duplicates


def _score_cases(cases: list[ReceiptCase], registry: dict[str, Any]) -> None:
    for case in cases:
        assert case.crops is not None
        _letter_count, evidence = score_typeface_sources(case.crops, registry)
        case.scores = {
            name: source.score for name, source in sorted(evidence.items())
        }


def _source_evidence(
    source: str,
    cases: list[ReceiptCase],
    *,
    foreign_cases: list[ReceiptCase] | None = None,
) -> dict[str, Any] | None:
    genuine = [
        case
        for case in cases
        if case.split == "calibration" and source in case.true_sources
    ]
    if source == "MERCHANT:vons":
        genuine = [
            case for case in genuine if case.key not in _VONS_ATLAS_EXCLUSIONS
        ]
    impostor_pool = foreign_cases if foreign_cases is not None else cases
    impostors = [
        case
        for case in impostor_pool
        if case.split == "calibration" and source not in case.true_sources
    ]
    holdout_genuine = [
        case
        for case in cases
        if case.split == "holdout" and source in case.true_sources
    ]
    if source == "MERCHANT:vons":
        holdout_genuine = [
            case
            for case in holdout_genuine
            if case.key not in _VONS_ATLAS_EXCLUSIONS
        ]
    holdout_impostors = [
        case
        for case in impostor_pool
        if case.split == "holdout" and source not in case.true_sources
    ]
    if not genuine:
        return None

    def complete_scores(cohort: list[ReceiptCase], split: str) -> list[float]:
        missing = [
            case.case_id
            for case in cohort
            if case.scores is None or source not in case.scores
        ]
        if missing:
            raise RuntimeError(
                f"{source} lacks {split} scores for {sorted(missing)}"
            )
        return [
            case.scores[source]  # type: ignore[index]
            for case in sorted(cohort, key=lambda item: item.case_id)
        ]

    genuine_scores = complete_scores(genuine, "calibration genuine")
    impostor_scores = complete_scores(impostors, "calibration impostor")
    holdout_genuine_scores = complete_scores(
        holdout_genuine, "holdout genuine"
    )
    holdout_impostor_scores = complete_scores(
        holdout_impostors, "holdout impostor"
    )
    if not impostor_scores:
        raise RuntimeError(f"{source} has no calibration impostors")
    gap = min(genuine_scores) - max(impostor_scores)
    calibration_hashes = sorted({case.case_id for case in genuine + impostors})
    holdout_hashes = sorted(
        {case.case_id for case in holdout_genuine + holdout_impostors}
    )
    threshold = (max(impostor_scores) + min(genuine_scores)) / 2.0
    holdout_passes = (
        bool(holdout_genuine_scores)
        and bool(holdout_impostor_scores)
        and min(holdout_genuine_scores) >= threshold
        and max(holdout_impostor_scores) < threshold
    )
    if gap <= 0:
        disabled_reason = "NO_GENUINE_IMPOSTOR_SEPARATION_EXACT_RUNTIME"
    elif holdout_passes:
        disabled_reason = "MEASURED_GATES_PASSED_PENDING_REGISTRY_BUILD"
    else:
        disabled_reason = "UNTOUCHED_HOLDOUT_DID_NOT_PASS"
    return {
        "disabled_reason": disabled_reason,
        "calibration_genuine_scores": genuine_scores,
        "calibration_impostor_scores": impostor_scores,
        "calibration_gap": gap,
        "calibration_receipt_hashes": calibration_hashes,
        "holdout_receipt_hashes": holdout_hashes,
        "holdout_genuine_scores": holdout_genuine_scores,
        "holdout_impostor_scores": holdout_impostor_scores,
    }


def _fixture(case: ReceiptCase, label: str) -> dict[str, Any]:
    assert case.crops is not None
    assert case.scores is not None
    by_character: dict[str, list[np.ndarray]] = {}
    for char, mask in case.crops:
        if len(char) == 1 and char.strip():
            by_character.setdefault(char, []).append(
                np.asarray(mask, dtype=bool)
            )
    glyphs = {
        char: [_encode(mask) for mask in masks]
        for char, masks in sorted(by_character.items())
    }
    return {
        "label": label,
        "case_id": case.case_id,
        "crop_digest": case.crop_digest,
        "expected_typeface": None,
        "expected_abstention_reason": "NO_SEPARATED_SOURCE_MATCH",
        "expected_source_scores": case.scores,
        "expected_source_scores_sha256": _json_sha256(case.scores),
        "expected_top_source": max(case.scores, key=case.scores.__getitem__),
        "glyphs": glyphs,
    }


def evaluate(table_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the guarded read-only measurement and return JSON-safe results."""

    if table_name == _PROD_TABLE or table_name != _DEV_TABLE:
        raise RuntimeError(
            f"refusing table {table_name!r}; only {_DEV_TABLE!r} is allowed"
        )
    validation = json.loads(_VALIDATION_PATH.read_text(encoding="utf-8"))
    client = DynamoClient(table_name)
    s3 = boto3.client("s3")
    base = build_atlas_registry()
    score_registry = {
        "atlases": base["entries"],
        "atlas_registry_sha256": base["atlas_registry_sha256"],
    }
    validation_cases = _validation_cases(validation)
    costco_cases, costco_manifest_sha256 = _costco_cases(client)
    all_cases = validation_cases + costco_cases
    missing = []
    details_cache: dict[str, Any] = {}
    populated = []
    for case in all_cases:
        try:
            _populate_case(case, client, s3, details_cache)
            populated.append(case)
        except (EntityNotFoundError, RuntimeError) as exc:
            if case.merchant == "Costco Wholesale":
                raise RuntimeError(
                    f"pinned Costco case {case.case_id} could not be populated"
                ) from exc
            missing.append({"case_id": case.case_id, "reason": str(exc)})
    validation_present = [
        case for case in populated if case.merchant != "Costco Wholesale"
    ]
    costco_present = [
        case for case in populated if case.merchant == "Costco Wholesale"
    ]
    validation_unique, validation_duplicates = _dedupe_and_split(
        validation_present
    )
    costco_unique, costco_duplicates = _dedupe_and_split(costco_present)
    if costco_duplicates or len(costco_unique) != len(_COSTCO_CASE_IDS):
        raise RuntimeError(
            "pinned Costco cohort drifted after crop deduplication: "
            f"unique={len(costco_unique)}, duplicates={costco_duplicates}"
        )
    _score_cases(validation_unique, score_registry)
    _score_cases(costco_unique, score_registry)
    sources = {}
    covered_sources = sorted(
        {source for case in validation_unique for source in case.true_sources}
    )
    for source in covered_sources:
        evidence = _source_evidence(source, validation_unique)
        if evidence is not None:
            sources[source] = evidence
    costco_evidence = _source_evidence(
        "MERCHANT:costco",
        costco_unique,
        foreign_cases=validation_unique,
    )
    if costco_evidence is not None:
        sources["MERCHANT:costco"] = costco_evidence

    measurement = {
        "schema_version": 2,
        "atlas_registry_sha256": base["atlas_registry_sha256"],
        "calibration": {
            "table": table_name,
            "extractor": (
                "receipt_upload.typeface_fingerprint.extract_letter_crops"
            ),
            "feature": (
                "canonical-normalize; per-character-median-shifted-iou; "
                "median-across-distinct-characters"
            ),
            "minimum_distinct_characters": 6,
            "minimum_distinct_characters_derivation": (
                "With five paired characters, a unanimous exact two-sided "
                "sign test has p=0.0625; six reach p=0.03125 at alpha=0.05."
            ),
            "split_policy": (
                "score-blind merchant-stratified alternating split after "
                "exact normalized-crop-multiset deduplication"
            ),
            "threshold_policy": (
                "strict calibration gap; midpoint threshold; untouched "
                "holdout required before enablement"
            ),
        },
        "corpus": {
            "validation_manifest_sha256": hashlib.sha256(
                _VALIDATION_PATH.read_bytes()
            ).hexdigest(),
            "costco_manifest_sha256": costco_manifest_sha256,
            "costco_exclusions": sorted(
                hashlib.sha256(key.encode()).hexdigest()[:12]
                for key in _COSTCO_EXCLUSIONS
            ),
            "listed_count": len(all_cases),
            "present_count": len(populated),
            "validation_unique_count": len(validation_unique),
            "costco_unique_count": len(costco_unique),
            "crop_digest_multiset_sha256": _json_sha256(
                sorted(case.crop_digest for case in populated)
            ),
            "costco_crop_digest_multiset_sha256": _json_sha256(
                sorted(case.crop_digest for case in costco_unique)
            ),
            "missing": sorted(missing, key=lambda item: item["case_id"]),
            "duplicates": sorted(
                validation_duplicates + costco_duplicates,
                key=lambda item: item["case_id"],
            ),
        },
        "sources": sources,
    }
    selected_fixture_cases: list[tuple[ReceiptCase, str]] = []
    for merchant, label in (
        ("Costco Wholesale", "costco-heldout-no-separated-source"),
        ("Smith's", "foreign-smiths-heldout"),
        ("The Home Depot", "homedepot-heldout-ambiguity"),
    ):
        case = next(
            item
            for item in validation_unique + costco_unique
            if item.merchant == merchant and item.split == "holdout"
        )
        selected_fixture_cases.append((case, label))
    measurement["runtime_fixture_cases"] = {
        case.case_id: {
            "crop_digest": case.crop_digest,
            "source_scores_sha256": _json_sha256(case.scores),
        }
        for case, _label in selected_fixture_cases
    }
    fixture_cases = [
        _fixture(case, label) for case, label in selected_fixture_cases
    ]
    fixtures = {
        "schema_version": 2,
        "measurement_sha256": _json_sha256(measurement),
        "atlas_registry_sha256": measurement["atlas_registry_sha256"],
        "privacy": (
            "cleaned binary per-character crop masks only; no receipt image, "
            "UUID, S3 path, or raw OCR geometry"
        ),
        "cases": fixture_cases,
    }
    return measurement, fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table", default=os.environ.get("DYNAMODB_TABLE_NAME", _DEV_TABLE)
    )
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument(
        "--fixtures-output", type=Path, default=_DEFAULT_FIXTURES
    )
    args = parser.parse_args()
    measurement, fixtures = evaluate(args.table)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.fixtures_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(measurement, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.fixtures_output.write_text(
        json.dumps(fixtures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.output}; sources={len(measurement['sources'])}; "
        f"fixtures={len(fixtures['cases'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

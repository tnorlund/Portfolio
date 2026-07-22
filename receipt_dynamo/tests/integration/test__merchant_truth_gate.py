"""Integration coverage for MERCHANT_TRUTH_GATE append-only records."""

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth_gate import MerchantTruthGateRecord

pytestmark = pytest.mark.integration

SLUG = "costco-wholesale"
HASH_A = "a" * 64
HASH_B = "b" * 64


def record(run_at: str, version: int, bundle_hash: str, **overrides):
    kwargs = dict(
        slug=SLUG,
        run_at=run_at,
        version=version,
        bundle_hash=bundle_hash,
        eval_git_sha="deadbeefcafe",
        overall="PASS",
        per_metric={"columns": "PASS", "logo": "PASS"},
        gaps=[],
        evidence_refs=["/out/costco.checks.json"],
        receipt_tested={"image_id": "img-1", "receipt_id": 1},
    )
    kwargs.update(overrides)
    return MerchantTruthGateRecord(**kwargs)


def test_two_runs_list_in_order(dynamodb_table: str):
    client = DynamoClient(dynamodb_table)
    earlier = record("2026-07-22T10:00:00+00:00", 1, HASH_A)
    later = record(
        "2026-07-22T12:00:00+00:00",
        1,
        HASH_A,
        overall="PASS_WITH_GAPS",
        per_metric={"columns": "PASS", "logo": "PASS_WITH_GAPS"},
        gaps=[
            {"metric": "logo", "verdict": "PASS_WITH_GAPS", "detail": "faint"}
        ],
    )
    # Insert out of order to prove the query orders by SK, not insert order.
    client.add_gate_record(later, dynamodb_table)
    client.add_gate_record(earlier, dynamodb_table)

    ascending = client.list_gate_records(SLUG)
    assert [r.run_at for r in ascending] == [earlier.run_at, later.run_at]
    assert ascending[0] == earlier
    assert ascending[1] == later

    descending = client.list_gate_records(SLUG, ascending=False)
    assert [r.run_at for r in descending] == [later.run_at, earlier.run_at]


def test_append_only_conflict_on_identical_sk(dynamodb_table: str):
    client = DynamoClient(dynamodb_table)
    first = record("2026-07-22T10:00:00+00:00", 1, HASH_A)
    client.add_gate_record(first, dynamodb_table)
    # Same (slug, run_at, version) -> identical SK -> append-only conflict,
    # even with a different bundle hash / payload.
    clash = record("2026-07-22T10:00:00+00:00", 1, HASH_B)
    with pytest.raises(MerchantTruthConflictError):
        client.add_gate_record(clash, dynamodb_table)
    # The original is untouched (no silent overwrite).
    stored = client.list_gate_records(SLUG)
    assert len(stored) == 1
    assert stored[0].bundle_hash == HASH_A


def test_gsitype_enumerates_across_merchants(dynamodb_table: str):
    client = DynamoClient(dynamodb_table)
    client.add_gate_record(
        record("2026-07-22T10:00:00+00:00", 1, HASH_A), dynamodb_table
    )
    other = MerchantTruthGateRecord(
        slug="sprouts-farmers-market",
        run_at="2026-07-22T11:00:00+00:00",
        version=2,
        bundle_hash=HASH_B,
        eval_git_sha="feedface",
        overall="FAIL",
        per_metric={"columns": "FAIL"},
        gaps=[{"metric": "columns", "verdict": "FAIL", "detail": "wobble"}],
        evidence_refs=["/out/sprouts.checks.json"],
        receipt_tested={"image_id": "img-2", "receipt_id": 3},
    )
    client.add_gate_record(other, dynamodb_table)

    everything = client.list_all_gate_records()
    slugs = sorted(r.slug for r in everything)
    assert slugs == ["costco-wholesale", "sprouts-farmers-market"]
    # A per-merchant list stays scoped to its partition.
    assert {r.slug for r in client.list_gate_records(SLUG)} == {SLUG}


def test_write_refuses_wrong_table(dynamodb_table: str):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(MerchantTruthTableMismatchError):
        client.add_gate_record(
            record("2026-07-22T10:00:00+00:00", 1, HASH_A),
            "some-other-table",
        )

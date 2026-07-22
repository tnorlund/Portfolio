"""End-to-end moto coverage for the §7.5 eval->seal gate bridge.

Mints an OPEN version and seals it *through the bridge*: a PASS_WITH_GAPS
eval payload seals with its gap list carried verbatim onto the sealed
manifest, and a FAIL payload blocks the seal so the version stays OPEN.
"""

from __future__ import annotations

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.merchant_truth_gate_bridge import (
    GateBlockedError,
    bridge_eval_to_gate_results,
)
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthComponent,
)

pytestmark = pytest.mark.integration
SLUG = "costco-wholesale"
NOW = "2026-07-22T16:00:00+00:00"
LEGACY_PROVENANCE = {
    "source_kind": "migration",
    "provenance_completeness": "legacy",
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    },
}
MINT_PROVENANCE = {
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    }
}


def _mint_open(client: DynamoClient, table: str, version: int) -> None:
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=version,
            name=name,
            payload={"component": name, "v": version},
            provenance=dict(LEGACY_PROVENANCE),
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    client.mint_version(
        SLUG,
        version,
        components,
        MINT_PROVENANCE,
        f"run-{version}",
        table,
        created_at=NOW,
    )


def _eval_pass_with_gaps() -> dict:
    return {
        "columns": {"verdict": "PASS"},
        "style": {"verdict": "PASS"},
        "tokens": {"verdict": "PASS"},
        "separators": {"verdict": "PASS_WITH_GAPS", "score": 0.72},
        "graphics": {"verdict": "PASS"},
        "logo": {"verdict": "UNTESTED", "note": "no storefront ink in real"},
        "arithmetic": {"verdict": "PASS"},
        "coverage_gaps": ["logo", "separators"],
        "overall": "PASS_WITH_GAPS",
    }


def test_seal_through_bridge_carries_gaps_verbatim(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    _mint_open(client, dynamodb_table, 1)

    gate_results = bridge_eval_to_gate_results(_eval_pass_with_gaps())
    sealed = client.seal_version(
        SLUG, 1, gate_results, [], dynamodb_table, sealed_at=NOW
    )

    assert sealed.status == "SEALED"
    assert sealed.gate_status == "PASS"

    reloaded = client.get_merchant_truth_manifest(
        SLUG, 1, consistent_read=True
    )
    assert reloaded is not None
    stored = reloaded.gate_results
    assert stored["overall"] == "PASS_WITH_GAPS"
    gaps = {gap["metric"]: gap for gap in stored["gaps"]}
    assert set(gaps) == {"separators", "logo"}
    assert gaps["logo"]["verdict"] == "UNTESTED"
    assert gaps["logo"]["detail"] == {"note": "no storefront ink in real"}
    assert gaps["separators"]["detail"] == {"score": 0.72}


def test_fail_payload_blocks_seal_and_leaves_version_open(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    _mint_open(client, dynamodb_table, 1)

    failing = {
        "columns": {"verdict": "FAIL", "detail": "cols drift"},
        "tokens": {"verdict": "PASS"},
        "overall": "FAIL",
    }
    with pytest.raises(GateBlockedError):
        gate_results = bridge_eval_to_gate_results(failing)
        client.seal_version(
            SLUG, 1, gate_results, [], dynamodb_table, sealed_at=NOW
        )

    reloaded = client.get_merchant_truth_manifest(
        SLUG, 1, consistent_read=True
    )
    assert reloaded is not None
    assert reloaded.status == "OPEN"

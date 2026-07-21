"""Request-level contracts for governed MerchantTruth writes."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data._merchant_truth import CREATE_ONLY, _MerchantTruth
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthIntegrityError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    MerchantTruthProposal,
    compute_bundle_hash,
)

pytestmark = pytest.mark.unit
TABLE = "ReceiptsTable-test"
SLUG = "sprouts-farmers-market"
NOW = "2026-07-20T16:00:00+00:00"


class RecordingClient:
    """Small low-level client that records exact generated requests."""

    def __init__(self) -> None:
        self.transactions: list[dict[str, Any]] = []
        self.puts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.get_responses: list[dict[str, Any]] = []
        self.query_responses: list[dict[str, Any]] = []
        self.transaction_errors: list[ClientError] = []

    def transact_write_items(self, **kwargs: Any) -> None:
        self.transactions.append(kwargs)
        if self.transaction_errors:
            raise self.transaction_errors.pop(0)

    def put_item(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)

    def update_item(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def get_item(self, **_kwargs: Any) -> dict[str, Any]:
        return self.get_responses.pop(0) if self.get_responses else {}

    def query(self, **_kwargs: Any) -> dict[str, Any]:
        return self.query_responses.pop(0) if self.query_responses else {}


def accessor(client: RecordingClient | None = None) -> _MerchantTruth:
    value = _MerchantTruth()
    value.table_name = TABLE
    value._client = client or RecordingClient()
    return value


def components(version: int = 1) -> list[MerchantTruthComponent]:
    return [
        MerchantTruthComponent(
            slug=SLUG,
            version=version,
            name=name,
            payload={"component": name, "value": version},
            provenance={
                "source_kind": "migration",
                "provenance_completeness": "legacy",
            },
        )
        for name in sorted(COMPONENT_NAMES)
    ]


def manifest(version: int = 1, status: str = "OPEN") -> MerchantTruthManifest:
    hashes = {item.name: item.content_hash for item in components(version)}
    return MerchantTruthManifest(
        slug=SLUG,
        version=version,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status=status,
        provenance={"written_by": "test"},
        mint_run_id=f"run-{version}",
        gate_status="PASS" if status == "SEALED" else "PENDING",
    )


def active(
    version: int, digest: str, actor: str = "owner"
) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=SLUG,
        version=version,
        bundle_hash=digest,
        normalized_aliases=["sprouts", "sprouts farmers market"],
        activated_at=NOW,
        activated_by=actor,
    )


def transaction_conflict() -> ClientError:
    return ClientError(
        {"Error": {"Code": "TransactionCanceledException"}},
        "TransactWriteItems",
    )


def test_atomic_mint_is_one_nine_action_conditional_transaction() -> None:
    client = RecordingClient()
    truth = accessor(client)

    result = truth.mint_version(
        SLUG,
        1,
        components(),
        {"written_by": "migration"},
        "run-1",
        TABLE,
        created_at=NOW,
    )

    assert result.status == "OPEN"
    assert len(client.transactions) == 1
    actions = client.transactions[0]["TransactItems"]
    assert len(actions) == 9
    assert all(
        action["Put"]["ConditionExpression"] == CREATE_ONLY
        for action in actions
    )
    assert client.transactions[0]["ClientRequestToken"]


def test_overflow_retry_resumes_same_manifest_and_skips_existing_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    truth = accessor(client)
    expected = manifest()
    existing = components()[0]
    client.transaction_errors.append(transaction_conflict())
    client.get_responses.append({"Item": expected.to_item()})
    client.query_responses.append({"Items": [existing.to_item()]})
    monkeypatch.setattr(truth, "_fits_atomic_mint", lambda _actions: False)

    result = truth.mint_version(
        SLUG,
        1,
        components(),
        {"written_by": "test"},
        "run-1",
        TABLE,
        created_at=NOW,
    )

    assert result == expected
    assert len(client.transactions) == 2
    resumed = client.transactions[1]["TransactItems"]
    assert "ConditionCheck" in resumed[0]
    assert resumed[0]["ConditionCheck"]["ConditionExpression"] == (
        "#status = :open AND mint_run_id = :run_id AND "
        "bundle_hash = :bundle_hash"
    )
    written_components = {
        action["Put"]["Item"].get("component", {}).get("S")
        for action in resumed[1:]
    }
    assert existing.name not in written_components
    assert written_components == COMPONENT_NAMES - {existing.name}


def test_seal_verifies_open_bundle_and_conditions_the_update() -> None:
    client = RecordingClient()
    truth = accessor(client)
    expected = manifest()
    client.get_responses.append({"Item": expected.to_item()})
    client.query_responses.append(
        {"Items": [item.to_item() for item in components()]}
    )

    sealed = truth.seal_version(
        SLUG,
        1,
        {"status": "PASS", "report": "s3://eval/pass.json"},
        ["PROPOSED#one"],
        TABLE,
        sealed_at=NOW,
    )

    assert sealed.status == "SEALED"
    assert sealed.gate_status == "PASS"
    actions = client.transactions[0]["TransactItems"]
    assert len(actions) == 2
    update = actions[0]["Update"]
    assert update["ConditionExpression"] == (
        "#status = :open AND bundle_hash = :bundle_hash"
    )
    assert update["ExpressionAttributeValues"][":open"] == {"S": "OPEN"}
    assert update["ExpressionAttributeValues"][":gate_status"] == {"S": "PASS"}
    assert actions[1]["Put"]["ConditionExpression"] == CREATE_ONLY


@pytest.mark.parametrize(
    "gate_results",
    [
        pytest.param({"status": "FAIL", "report": "s3://eval/x"}, id="fail"),
        pytest.param({"passed": False}, id="passed-false"),
        pytest.param({"report": "s3://eval/ambiguous.json"}, id="ambiguous"),
        pytest.param({}, id="empty"),
        pytest.param(
            {"status": "PASS", "passed": False}, id="contradictory"
        ),
    ],
)
def test_seal_fails_closed_when_gate_did_not_pass(
    gate_results: dict[str, Any],
) -> None:
    client = RecordingClient()
    truth = accessor(client)
    expected = manifest()
    client.get_responses.append({"Item": expected.to_item()})
    client.query_responses.append(
        {"Items": [item.to_item() for item in components()]}
    )

    with pytest.raises(
        (MerchantTruthConflictError, MerchantTruthIntegrityError)
    ):
        truth.seal_version(
            SLUG, 1, gate_results, [], TABLE, sealed_at=NOW
        )

    assert client.transactions == []


def test_seal_rejects_a_non_open_manifest_before_writing() -> None:
    client = RecordingClient()
    client.get_responses.append({"Item": manifest(status="SEALED").to_item()})
    truth = accessor(client)

    with pytest.raises(MerchantTruthConflictError, match="OPEN"):
        truth.seal_version(SLUG, 1, {}, [], TABLE, sealed_at=NOW)

    assert client.transactions == []


def test_initial_activation_uses_conditional_put_for_absent_active() -> None:
    client = RecordingClient()
    truth = accessor(client)
    target = active(1, manifest(status="SEALED").bundle_hash)

    truth.initial_activate(target, TABLE)

    actions = client.transactions[0]["TransactItems"]
    assert len(actions) == 3
    assert actions[0]["ConditionCheck"]["ExpressionAttributeValues"][
        ":sealed"
    ] == {"S": "SEALED"}
    assert actions[1]["Put"]["Item"]["SK"] == {"S": "TRUTH#ACTIVE"}
    assert actions[1]["Put"]["ConditionExpression"] == CREATE_ONLY
    assert actions[2]["Put"]["ConditionExpression"] == CREATE_ONLY


def test_subsequent_flip_conditions_on_exact_version_and_hash() -> None:
    client = RecordingClient()
    old_manifest = manifest(1, status="SEALED")
    new_manifest = manifest(2, status="SEALED")
    old_active = active(1, old_manifest.bundle_hash)
    target = active(2, new_manifest.bundle_hash)
    client.get_responses.append({"Item": old_active.to_item()})
    truth = accessor(client)

    truth.flip_active(target, 1, old_manifest.bundle_hash, TABLE)

    update = client.transactions[0]["TransactItems"][1]["Update"]
    assert update["ConditionExpression"] == (
        "version = :expected_version AND "
        "bundle_hash = :expected_bundle_hash"
    )
    values = update["ExpressionAttributeValues"]
    assert values[":expected_version"] == {"N": "1"}
    assert values[":expected_bundle_hash"] == {"S": old_manifest.bundle_hash}


def test_proposal_create_and_resolution_pass_explicit_conditions() -> None:
    client = RecordingClient()
    truth = accessor(client)
    proposal = MerchantTruthProposal(
        slug=SLUG,
        created_at=NOW,
        claim_slug="bold-headers",
        claim="headers are bold",
    )

    truth.add_proposal(proposal, TABLE)
    truth.resolve_proposal(proposal, 2, "confirmed", TABLE)

    assert client.puts[0]["ConditionExpression"] == CREATE_ONLY
    assert client.updates[0]["ConditionExpression"] == "#status = :open"
    assert client.updates[0]["ExpressionAttributeValues"][":candidate"] == {
        "S": "MEASURED_IN_CANDIDATE"
    }


def test_dev_table_guard_fails_before_any_write() -> None:
    client = RecordingClient()
    truth = accessor(client)

    with pytest.raises(MerchantTruthTableMismatchError, match="refusing"):
        truth.mint_version(
            SLUG,
            1,
            components(),
            {"written_by": "test"},
            "run-1",
            "ReceiptsTable-dc5be22",
            created_at=NOW,
        )

    assert client.transactions == []

"""Unit tests for the read-only upload-determinism evaluator."""

import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_upload.section_assignment import RowAssignment
from scripts.evaluate_upload_determinism import (
    CorpusReceipt,
    _finalize_section_metrics,
    _majority_row_label,
    _new_section_metrics,
    _score_sections,
    assert_dev_table,
    assert_private_destination,
    assign_folds,
    project_reconciled_labels,
    select_manual_sample,
    write_private_text,
)
from scripts.render_upload_determinism_audit import (
    _bucket_allowlist,
    _image,
    _prepare_private_directory,
    _save_private_png,
)

_UUID = "11111111-1111-4111-8111-111111111111"


def _receipt(image_id: str, merchant: str = "Example") -> CorpusReceipt:
    return CorpusReceipt(
        image_id=image_id,
        receipt_id=1,
        merchant_name=merchant,
        lines=(),
        words=(),
        labels=(),
        rows=(),
        gold_sections=(),
        labeled_rows=(),
    )


def test_dev_table_guard_requires_both_dev_tags() -> None:
    dynamodb = Mock()
    dynamodb.describe_table.return_value = {
        "Table": {"TableArn": "arn:example:table/dev"}
    }
    dynamodb.list_tags_of_resource.return_value = {
        "Tags": [
            {"Key": "Environment", "Value": "dev"},
            {"Key": "Pulumi_Stack", "Value": "dev"},
        ]
    }
    with patch(
        "scripts.evaluate_upload_determinism.boto3.client",
        return_value=dynamodb,
    ):
        assert assert_dev_table("dev", "table-from-env") == "table-from-env"
        with pytest.raises(RuntimeError, match="exactly dev"):
            assert_dev_table("prod", "table-from-env")


@pytest.mark.parametrize(
    "tags",
    [
        [{"Key": "Pulumi_Stack", "Value": "dev"}],
        [{"Key": "Environment", "Value": "dev"}],
        [
            {"Key": "Environment", "Value": "prod"},
            {"Key": "Pulumi_Stack", "Value": "dev"},
        ],
        [
            {"Key": "Environment", "Value": "dev"},
            {"Key": "Pulumi_Stack", "Value": "prod"},
        ],
    ],
)
def test_dev_table_guard_rejects_missing_or_wrong_tags(
    tags: list[dict[str, str]],
) -> None:
    dynamodb = Mock()
    dynamodb.describe_table.return_value = {
        "Table": {"TableArn": "arn:example:table/not-dev"}
    }
    dynamodb.list_tags_of_resource.return_value = {"Tags": tags}
    with patch(
        "scripts.evaluate_upload_determinism.boto3.client",
        return_value=dynamodb,
    ), pytest.raises(RuntimeError, match="Environment=dev.*Pulumi_Stack=dev"):
        assert_dev_table("dev", "table-from-env")


def test_private_evaluation_output_stays_outside_repo_and_is_owner_only(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="outside the repository"):
        assert_private_destination(
            Path(__file__).resolve().parent / "private-evaluation.json"
        )

    destination = tmp_path / "new-private-dir" / "evaluation.json"
    written = write_private_text(destination, "private receipt data\n")

    assert written.read_text(encoding="utf-8") == "private receipt data\n"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(written.parent.stat().st_mode) == 0o700


def test_audit_directory_and_png_are_owner_only(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="outside the repository"):
        _prepare_private_directory(
            Path(__file__).resolve().parent / "private-audit"
        )

    output_dir = _prepare_private_directory(
        tmp_path / "private-parent" / "private-audit"
    )
    output = output_dir / "01.png"
    _save_private_png(Image.new("RGB", (2, 2), "white"), output)

    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(output_dir.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_audit_s3_bucket_allowlist_is_required_and_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="AUDIT_S3_BUCKETS.*required"):
        _bucket_allowlist(None)

    receipt = SimpleNamespace(
        cdn_s3_bucket="prod-cdn",
        cdn_s3_key="receipt.png",
        raw_s3_bucket="prod-raw",
        raw_s3_key="receipt.png",
        image_id=_UUID,
        receipt_id=1,
    )
    s3 = Mock()
    with patch(
        "scripts.render_upload_determinism_audit.boto3.client",
        return_value=s3,
    ):
        with pytest.raises(RuntimeError, match="image unavailable"):
            _image(receipt, _bucket_allowlist("dev-cdn,dev-raw"))

    s3.get_object.assert_not_called()


def test_fold_assignment_keeps_source_image_together() -> None:
    first = _receipt(_UUID)
    second = CorpusReceipt(**{**first.__dict__, "receipt_id": 2})
    other = _receipt("22222222-2222-4222-8222-222222222222")

    folds = assign_folds([first, second, other])

    assert folds[first.image_id] == folds[second.image_id]
    assert set(folds.values()) <= set(range(5))


def test_row_label_ties_match_production_primary_then_sorted_policy() -> None:
    primary_wins = SimpleNamespace(row_id=2, line_ids=[1, 2])
    sorted_fallback = SimpleNamespace(row_id=99, line_ids=[1, 2])
    gold = {1: "ITEMS", 2: "HEADER"}

    assert _majority_row_label(primary_wins, gold) == "HEADER"
    assert _majority_row_label(sorted_fallback, gold) == "HEADER"


def test_section_metrics_score_unique_valid_gold_only() -> None:
    row = SimpleNamespace(row_id=1, line_ids=[1])
    gold = SimpleNamespace(
        validation_status=ValidationStatus.VALID.value,
        section_type="ITEMS",
        line_ids=[1],
    )
    receipt = CorpusReceipt(
        **{
            **_receipt(_UUID).__dict__,
            "rows": (row,),
            "gold_sections": (gold,),
        }
    )
    metrics = _new_section_metrics()

    _score_sections(
        metrics,
        receipt,
        [RowAssignment(row=row, section_type="ITEMS", confidence=0.9)],
    )
    result = _finalize_section_metrics(metrics)

    assert result["eligible_lines"] == 1
    assert result["line_micro_accuracy"] == 1.0
    assert result["row_accuracy"] == 1.0
    assert result["eligible_gold_exact_receipt_rate"] == 1.0


def test_reconciliation_projection_never_mutates_input() -> None:
    original = ReceiptWordLabel(
        image_id=_UUID,
        receipt_id=1,
        line_id=1,
        word_id=1,
        label="PRODUCT_NAME",
        reasoning="layoutlm",
        timestamp_added=datetime.now(timezone.utc),
        validation_status=ValidationStatus.PENDING.value,
        label_proposed_by="layoutlm",
    )
    plan = SimpleNamespace(
        corrections=[
            {
                "line_id": 1,
                "word_id": 1,
                "original_label": "PRODUCT_NAME",
                "original_new_status": "INVALID",
                "corrected_label": "GRAND_TOTAL",
                "corrected_status": "PENDING",
                "reason": "test correction",
                "provenance": "section-rule",
            }
        ]
    )

    projected = project_reconciled_labels([original], plan, _UUID, 1)

    assert original.validation_status == ValidationStatus.PENDING.value
    assert projected[0].validation_status == ValidationStatus.INVALID.value
    assert projected[1].label == "GRAND_TOTAL"
    assert projected[1].label_proposed_by.endswith(":section-rule")


def test_manual_sample_has_sentinels_and_three_frequency_strata() -> None:
    merchants = [
        "Sprouts Farmers Market",
        "Costco Wholesale",
        "Vons",
        "Smith's",
        "Italia Deli",
    ]
    counts = {merchant.casefold(): 30 for merchant in merchants}
    payloads = []
    for index, merchant in enumerate(merchants):
        payloads.append(
            {
                "image_id": f"{index + 1:08d}-0000-4000-8000-000000000000",
                "receipt_id": 1,
                "merchant": merchant,
            }
        )
    for index in range(30):
        merchant = f"Merchant {index}"
        key = merchant.casefold()
        counts[key] = 30 if index < 10 else 10 if index < 20 else 1
        payloads.append(
            {
                "image_id": f"{index + 100:08d}-0000-4000-8000-000000000000",
                "receipt_id": 1,
                "merchant": merchant,
            }
        )

    sample, design = select_manual_sample(payloads, counts)

    assert len(sample) == 20
    assert all(design["sentinels_found"].values())
    assert {item["sample_stratum"] for item in sample} >= {
        "common",
        "mid_frequency",
        "long_tail",
    }

from datetime import datetime, timezone

import pytest

from receipt_dynamo import (
    SyntheticReceiptVisualReview,
    item_to_synthetic_receipt_visual_review,
)


@pytest.fixture
def visual_review():
    return SyntheticReceiptVisualReview(
        review_id="9f6b97c5-0f24-45d7-a62f-86ab43ef3f8d",
        job_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        candidate_id="sprouts-add-line-item-abc",
        synthetic_image_id="sprouts-add-line-item",
        image_uri="/synthetic-receipts/sprouts.png",
        local_image_path="/tmp/sprouts.png",
        base_receipt_key="37333eb8-0025-4711-98ae-7b72fd83abd5#00001",
        merchant_name="Sprouts Farmers Market",
        operation="add_line_item",
        status="needs_iteration",
        reviewer="claude-mcp",
        reviewer_model="claude-sonnet",
        created_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
        realism_score=0.72,
        fidelity_score=0.8,
        alignment_score=0.67,
        issue_count=1,
        findings=[{"severity": "medium", "detail": "Inserted total is too dark"}],
        recommendations=["lighten restamped totals"],
        rubric={"thermal_noise": 0.7},
        metadata={"target_id": "sprouts-add-line-item"},
    )


@pytest.mark.unit
def test_synthetic_receipt_visual_review_keys(visual_review):
    assert visual_review.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {
            "S": (
                "SYNTHETIC_VISUAL_REVIEW#"
                "CANDIDATE#sprouts-add-line-item-abc#"
                "CREATED#2026-06-27T12:00:00+00:00#"
                "REVIEW#9f6b97c5-0f24-45d7-a62f-86ab43ef3f8d"
            )
        },
    }
    assert visual_review.gsi1_key()["GSI1PK"] == {
        "S": "SYNTHETIC_VISUAL_REVIEW#CANDIDATE#sprouts-add-line-item-abc"
    }
    assert visual_review.gsi2_key()["GSI2PK"] == {
        "S": "SYNTHETIC_VISUAL_REVIEW#STATUS#needs_iteration"
    }
    assert visual_review.gsi3_key()["GSI3PK"] == {
        "S": "SYNTHETIC_VISUAL_REVIEW#IMAGE#sprouts-add-line-item"
    }


@pytest.mark.unit
def test_synthetic_receipt_visual_review_round_trip(visual_review):
    item = visual_review.to_item()

    assert item["TYPE"] == {"S": "SYNTHETIC_RECEIPT_VISUAL_REVIEW"}
    assert item["realism_score"] == {"N": "0.72"}
    assert item["findings"]["L"][0]["M"]["severity"] == {"S": "medium"}

    parsed = item_to_synthetic_receipt_visual_review(item)
    assert parsed == visual_review
    assert parsed.findings == [
        {"severity": "medium", "detail": "Inserted total is too dark"}
    ]
    assert parsed.recommendations == ["lighten restamped totals"]
    assert parsed.rubric == {"thermal_noise": 0.7}


@pytest.mark.unit
def test_synthetic_receipt_visual_review_rejects_bad_status():
    with pytest.raises(ValueError, match="status must be one of"):
        SyntheticReceiptVisualReview(
            review_id="9f6b97c5-0f24-45d7-a62f-86ab43ef3f8d",
            job_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            candidate_id="candidate",
            status="maybe",
            reviewer="claude",
        )


@pytest.mark.unit
def test_synthetic_receipt_visual_review_rejects_bad_score():
    with pytest.raises(ValueError, match="realism_score must be between 0 and 1"):
        SyntheticReceiptVisualReview(
            review_id="9f6b97c5-0f24-45d7-a62f-86ab43ef3f8d",
            job_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            candidate_id="candidate",
            status="accepted",
            reviewer="claude",
            realism_score=1.2,
        )

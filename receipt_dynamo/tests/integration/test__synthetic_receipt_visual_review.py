import pytest

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.synthetic_receipt_visual_review import (
    SyntheticReceiptVisualReview,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def review_dynamo(dynamodb_table):
    return DynamoClient(table_name=dynamodb_table)


@pytest.fixture
def sample_visual_review():
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
        status="accepted",
        reviewer="claude-mcp",
        created_at="2026-06-27T12:00:00+00:00",
        realism_score=0.91,
        findings=[],
        recommendations=["keep current glyph fallback"],
    )


def test_add_and_list_synthetic_receipt_visual_review(
    review_dynamo,
    sample_visual_review,
):
    review_dynamo.add_synthetic_receipt_visual_review(sample_visual_review)

    by_job, _ = review_dynamo.list_synthetic_receipt_visual_reviews_for_job(
        sample_visual_review.job_id
    )
    assert by_job == [sample_visual_review]

    by_candidate, _ = (
        review_dynamo.list_synthetic_receipt_visual_reviews_for_candidate(
            sample_visual_review.candidate_id
        )
    )
    assert by_candidate == [sample_visual_review]

    by_status, _ = review_dynamo.list_synthetic_receipt_visual_reviews_by_status(
        "accepted"
    )
    assert by_status == [sample_visual_review]

    fetched = review_dynamo.get_synthetic_receipt_visual_review(
        sample_visual_review.job_id,
        sample_visual_review.candidate_id,
        sample_visual_review.created_at,
        sample_visual_review.review_id,
    )
    assert fetched == sample_visual_review


def test_list_synthetic_receipt_visual_reviews_for_job_can_filter_candidate(
    review_dynamo,
    sample_visual_review,
):
    other = SyntheticReceiptVisualReview(
        review_id="5cb7a7a0-0957-488f-8fa2-e269e74b3cbb",
        job_id=sample_visual_review.job_id,
        candidate_id="other-candidate",
        status="needs_iteration",
        reviewer="claude-mcp",
        created_at="2026-06-27T13:00:00+00:00",
    )
    review_dynamo.add_synthetic_receipt_visual_review(sample_visual_review)
    review_dynamo.add_synthetic_receipt_visual_review(other)

    rows, _ = review_dynamo.list_synthetic_receipt_visual_reviews_for_job(
        sample_visual_review.job_id,
        candidate_id=sample_visual_review.candidate_id,
    )

    assert rows == [sample_visual_review]

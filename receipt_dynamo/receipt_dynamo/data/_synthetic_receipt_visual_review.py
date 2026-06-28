"""Accessor methods for synthetic receipt visual review rows."""

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.synthetic_receipt_visual_review import (
    SyntheticReceiptVisualReview,
    item_to_synthetic_receipt_visual_review,
    synthetic_visual_review_key_part,
)


class _SyntheticReceiptVisualReview(FlattenedStandardMixin):
    """DynamoDB accessors for Claude visual receipt review state."""

    @handle_dynamodb_errors("add_synthetic_receipt_visual_review")
    def add_synthetic_receipt_visual_review(
        self, review: SyntheticReceiptVisualReview
    ) -> None:
        if review is None:
            raise EntityValidationError("review cannot be None")
        if not isinstance(review, SyntheticReceiptVisualReview):
            raise EntityValidationError(
                "review must be an instance of SyntheticReceiptVisualReview"
            )
        self._add_entity(
            review,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_synthetic_receipt_visual_review")
    def get_synthetic_receipt_visual_review(
        self,
        job_id: str,
        candidate_id: str,
        created_at: str,
        review_id: str,
    ) -> SyntheticReceiptVisualReview | None:
        self._validate_job_id(job_id)
        if not candidate_id:
            raise EntityValidationError("candidate_id must be a non-empty string")
        if not created_at:
            raise EntityValidationError("created_at must be a non-empty string")
        if not review_id:
            raise EntityValidationError("review_id must be a non-empty string")

        candidate_key = synthetic_visual_review_key_part(candidate_id)
        return self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key=(
                "SYNTHETIC_VISUAL_REVIEW#"
                f"CANDIDATE#{candidate_key}#"
                f"CREATED#{created_at}#REVIEW#{review_id}"
            ),
            entity_class=SyntheticReceiptVisualReview,
            converter_func=item_to_synthetic_receipt_visual_review,
        )

    @handle_dynamodb_errors("list_synthetic_receipt_visual_reviews_for_job")
    def list_synthetic_receipt_visual_reviews_for_job(
        self,
        job_id: str,
        candidate_id: str | None = None,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[SyntheticReceiptVisualReview], dict[str, Any] | None]:
        self._validate_job_id(job_id)
        self._validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )
        sk_prefix = "SYNTHETIC_VISUAL_REVIEW#"
        if candidate_id:
            sk_prefix = (
                "SYNTHETIC_VISUAL_REVIEW#"
                f"CANDIDATE#{synthetic_visual_review_key_part(candidate_id)}#"
            )

        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": sk_prefix},
            },
            converter_func=item_to_synthetic_receipt_visual_review,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,
        )

    @handle_dynamodb_errors("list_synthetic_receipt_visual_reviews_for_candidate")
    def list_synthetic_receipt_visual_reviews_for_candidate(
        self,
        candidate_id: str,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[SyntheticReceiptVisualReview], dict[str, Any] | None]:
        if not candidate_id:
            raise EntityValidationError("candidate_id must be a non-empty string")
        self._validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {
                    "S": (
                        "SYNTHETIC_VISUAL_REVIEW#"
                        "CANDIDATE#"
                        f"{synthetic_visual_review_key_part(candidate_id)}"
                    )
                },
            },
            converter_func=item_to_synthetic_receipt_visual_review,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,
        )

    @handle_dynamodb_errors("list_synthetic_receipt_visual_reviews_by_status")
    def list_synthetic_receipt_visual_reviews_by_status(
        self,
        status: str,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[SyntheticReceiptVisualReview], dict[str, Any] | None]:
        if not status:
            raise EntityValidationError("status must be a non-empty string")
        self._validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {
                    "S": (
                        "SYNTHETIC_VISUAL_REVIEW#"
                        f"STATUS#{str(status).strip().lower()}"
                    )
                },
            },
            converter_func=item_to_synthetic_receipt_visual_review,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,
        )

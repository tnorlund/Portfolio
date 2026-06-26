"""DynamoDB accessor mixin for SyntheticTrainingSet records."""

from typing import Any

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.synthetic_training_set import (
    VALID_SYNTHETIC_SET_STATUSES,
    SyntheticTrainingSet,
    item_to_synthetic_training_set,
)
from receipt_dynamo.entities.util import assert_valid_uuid


class _SyntheticTrainingSet(FlattenedStandardMixin):
    """Create/read/update synthetic training set records and links."""

    @handle_dynamodb_errors("add_synthetic_training_set")
    def add_synthetic_training_set(
        self, synthetic_set: SyntheticTrainingSet
    ) -> None:
        self._validate_entity(
            synthetic_set, SyntheticTrainingSet, "synthetic_set"
        )
        self._add_entity(
            synthetic_set,
            condition_expression="attribute_not_exists(PK)",
        )

    @handle_dynamodb_errors("update_synthetic_training_set")
    def update_synthetic_training_set(
        self, synthetic_set: SyntheticTrainingSet
    ) -> None:
        self._validate_entity(
            synthetic_set, SyntheticTrainingSet, "synthetic_set"
        )
        self._update_entity(
            synthetic_set,
            condition_expression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("get_synthetic_training_set")
    def get_synthetic_training_set(
        self, set_id: str
    ) -> SyntheticTrainingSet:
        if set_id is None:
            raise EntityValidationError("set_id cannot be None")
        assert_valid_uuid(set_id)
        result = self._get_entity(
            primary_key=f"SYNTHETIC_SET#{set_id}",
            sort_key="SYNTHETIC_SET",
            entity_class=SyntheticTrainingSet,
            converter_func=item_to_synthetic_training_set,
        )
        if result is None:
            raise EntityNotFoundError(
                f"SyntheticTrainingSet with id {set_id} does not exist"
            )
        return result

    @handle_dynamodb_errors("list_synthetic_training_sets_by_status")
    def list_synthetic_training_sets_by_status(
        self,
        status: str,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[SyntheticTrainingSet], dict | None]:
        if status not in VALID_SYNTHETIC_SET_STATUSES:
            raise EntityValidationError(
                f"status must be one of {VALID_SYNTHETIC_SET_STATUSES}"
            )
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :status",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values={
                ":status": {"S": f"SYNTH_SET_STATUS#{status}"},
                ":set_type": {"S": "SYNTHETIC_TRAINING_SET"},
            },
            converter_func=item_to_synthetic_training_set,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            filter_expression="#type = :set_type",
            scan_index_forward=False,
        )

    @handle_dynamodb_errors("get_synthetic_training_set_by_hash")
    def get_synthetic_training_set_by_hash(
        self, content_hash: str
    ) -> SyntheticTrainingSet | None:
        """Return the set recorded for this bundle content hash, or None.

        Used for dedup / reproducibility — the same bundle never gets two
        records.
        """
        if not content_hash:
            raise EntityValidationError("content_hash cannot be empty")
        results, _ = self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :hash",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values={
                ":hash": {"S": f"SYNTH_SET_HASH#{content_hash}"},
                ":set_type": {"S": "SYNTHETIC_TRAINING_SET"},
            },
            converter_func=item_to_synthetic_training_set,
            limit=1,
            filter_expression="#type = :set_type",
            scan_index_forward=False,
        )
        return results[0] if results else None

    @handle_dynamodb_errors("approve_synthetic_training_set")
    def approve_synthetic_training_set(
        self, set_id: str, approved_by: str, approved_at: str
    ) -> SyntheticTrainingSet:
        """Human-approval gate: mark a set approved for training."""
        synthetic_set = self.get_synthetic_training_set(set_id)
        synthetic_set.status = "approved"
        synthetic_set.approved_by = approved_by
        synthetic_set.approved_at = approved_at
        self.update_synthetic_training_set(synthetic_set)
        return synthetic_set

    @handle_dynamodb_errors("link_synthetic_training_set_to_job")
    def link_synthetic_training_set_to_job(
        self, set_id: str, job_id: str
    ) -> SyntheticTrainingSet:
        """Record that a training Job consumed this synthetic set."""
        synthetic_set = self.get_synthetic_training_set(set_id)
        if job_id not in synthetic_set.used_by_jobs:
            synthetic_set.used_by_jobs.append(job_id)
            self.update_synthetic_training_set(synthetic_set)
        return synthetic_set

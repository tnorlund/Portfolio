"""DynamoDB accessor mixin for SyntheticTrainingSet records."""

from typing import Any

from botocore.exceptions import ClientError

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

        Best-effort dedup / reproducibility lookup — call this before creating a
        record to avoid a duplicate for the same bundle. It is NOT a hard
        uniqueness constraint (concurrent creates could still race); the
        generation flow is sequential, so a check-then-create is sufficient.
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
        """Human-approval gate: atomically mark a set approved for training.

        Uses a single UpdateItem so it can't clobber a concurrent link/update,
        and moves the GSI1 partition (status) in the same atomic write.
        """
        assert_valid_uuid(set_id)
        response = self._client.update_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"SYNTHETIC_SET#{set_id}"},
                "SK": {"S": "SYNTHETIC_SET"},
            },
            UpdateExpression=(
                "SET #status = :status, GSI1PK = :gsi1pk, "
                "approved_by = :by, approved_at = :at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": {"S": "approved"},
                ":gsi1pk": {"S": "SYNTH_SET_STATUS#approved"},
                ":by": {"S": approved_by},
                ":at": {"S": approved_at},
            },
            ConditionExpression="attribute_exists(PK)",
            ReturnValues="ALL_NEW",
        )
        return SyntheticTrainingSet.from_item(response["Attributes"])

    @handle_dynamodb_errors("link_synthetic_training_set_to_job")
    def link_synthetic_training_set_to_job(
        self, set_id: str, job_id: str
    ) -> SyntheticTrainingSet:
        """Atomically record that a training Job consumed this synthetic set.

        Uses an atomic list_append guarded by ``NOT contains(used_by_jobs, job)``
        so concurrent links from different jobs cannot clobber each other and a
        repeated link is idempotent.
        """
        assert_valid_uuid(set_id)
        try:
            response = self._client.update_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"SYNTHETIC_SET#{set_id}"},
                    "SK": {"S": "SYNTHETIC_SET"},
                },
                UpdateExpression=(
                    "SET used_by_jobs = "
                    "list_append(if_not_exists(used_by_jobs, :empty), :job)"
                ),
                ExpressionAttributeValues={
                    ":job": {"L": [{"S": job_id}]},
                    ":empty": {"L": []},
                    ":jobval": {"S": job_id},
                },
                ConditionExpression=(
                    "attribute_exists(PK) AND "
                    "NOT contains(used_by_jobs, :jobval)"
                ),
                ReturnValues="ALL_NEW",
            )
            return SyntheticTrainingSet.from_item(response["Attributes"])
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                # Either already linked (idempotent no-op) or the set is
                # missing; get() returns it or raises EntityNotFoundError.
                return self.get_synthetic_training_set(set_id)
            raise

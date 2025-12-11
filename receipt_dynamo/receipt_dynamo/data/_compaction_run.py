"""
Accessor methods for CompactionRun items in DynamoDB.

Tracks per-run delta merges for Chroma collections (lines, words), aligned to
receipt keys (PK=IMAGE#<image_id>, SK=RECEIPT#<id>#COMPACTION_RUN#<run_id>).
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.compaction_run import (
    CompactionRun,
    item_to_compaction_run,
)

if TYPE_CHECKING:
    pass


class _CompactionRun(FlattenedStandardMixin):
    """Accessor methods for CompactionRun items in DynamoDB."""

    # ──────────────────────────── CRUD ─────────────────────────────
    @handle_dynamodb_errors("add_compaction_run")
    def add_compaction_run(self, run: CompactionRun) -> None:
        """Create a new compaction run record.

        Raises EntityValidationError if run is None or wrong type.
        """
        if run is None:
            raise EntityValidationError("run cannot be None")
        if not isinstance(run, CompactionRun):
            raise EntityValidationError(
                "run must be an instance of CompactionRun"
            )
        self._add_entity(run)

    @handle_dynamodb_errors("update_compaction_run")
    def update_compaction_run(self, run: CompactionRun) -> None:
        """Replace the compaction run record (full PUT with existence check)."""
        if run is None:
            raise EntityValidationError("run cannot be None")
        if not isinstance(run, CompactionRun):
            raise EntityValidationError(
                "run must be an instance of CompactionRun"
            )
        self._update_entity(
            run,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_compaction_run")
    def get_compaction_run(
        self, image_id: str, receipt_id: int, run_id: str
    ) -> Optional[CompactionRun]:
        """Fetch a compaction run by primary key (image, receipt, run)."""
        pk = f"IMAGE#{image_id}"
        sk = f"RECEIPT#{receipt_id:05d}#COMPACTION_RUN#{run_id}"
        return self._get_entity(
            primary_key=pk,
            sort_key=sk,
            entity_class=CompactionRun,
            converter_func=item_to_compaction_run,
        )

    # ──────────────────────────── QUERY ─────────────────────────────
    @handle_dynamodb_errors("list_compaction_runs_for_receipt")
    def list_compaction_runs_for_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompactionRun], Optional[Dict[str, Any]]]:
        """List runs for a specific receipt ordered by SK.

        Uses PK=IMAGE#<image_id> and begins_with on SK.
        """
        pk = f"IMAGE#{image_id}"
        sk_prefix = f"RECEIPT#{receipt_id:05d}#COMPACTION_RUN#"
        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": pk},
                ":sk": {"S": sk_prefix},
            },
            converter_func=item_to_compaction_run,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_recent_compaction_runs")
    def list_recent_compaction_runs(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompactionRun], Optional[Dict[str, Any]]]:
        """List recent runs using GSI1 (RUNS / CREATED_AT#...)."""
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={":pk": {"S": "RUNS"}},
            converter_func=item_to_compaction_run,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,
        )

    # ─────────────────────── convenience updates ─────────────────────
    @handle_dynamodb_errors("mark_compaction_run_started")
    def mark_compaction_run_started(
        self, image_id: str, receipt_id: int, run_id: str, collection: str
    ) -> None:
        """Mark a collection state as PROCESSING and set started_at timestamp."""
        run = self.get_compaction_run(image_id, receipt_id, run_id)
        if run is None:
            raise EntityNotFoundError("CompactionRun not found")
        now = datetime.now(timezone.utc).isoformat()
        if collection == "lines":
            run.lines_state = "PROCESSING"
            run.lines_started_at = now
        else:
            run.words_state = "PROCESSING"
            run.words_started_at = now
        self.update_compaction_run(run)

    @handle_dynamodb_errors("mark_compaction_run_completed")
    def mark_compaction_run_completed(
        self,
        image_id: str,
        receipt_id: int,
        run_id: str,
        collection: str,
        merged_vectors: int = 0,
    ) -> None:
        """Mark a collection state as COMPLETED and set finished_at + merged count.
        
        Uses atomic UpdateExpression to update only specific fields, preventing race conditions
        when both lines and words collection updates happen simultaneously.
        """
        pk = f"IMAGE#{image_id}"
        sk = f"RECEIPT#{receipt_id:05d}#COMPACTION_RUN#{run_id}"
        now = datetime.now(timezone.utc).isoformat()
        
        # Build UpdateExpression based on collection type
        if collection == "lines":
            update_expression = (
                "SET lines_state = :state, "
                "lines_finished_at = :finished_at, "
                "lines_merged_vectors = :merged_count, "
                "updated_at = :now"
            )
            expression_attribute_values = {
                ":state": {"S": "COMPLETED"},
                ":finished_at": {"S": now},
                ":merged_count": {"N": str(merged_vectors)},
                ":now": {"S": now},
            }
        else:  # words
            update_expression = (
                "SET words_state = :state, "
                "words_finished_at = :finished_at, "
                "words_merged_vectors = :merged_count, "
                "updated_at = :now"
            )
            expression_attribute_values = {
                ":state": {"S": "COMPLETED"},
                ":finished_at": {"S": now},
                ":merged_count": {"N": str(merged_vectors)},
                ":now": {"S": now},
            }
        
        # Atomic update - only modifies fields for this collection
        self._client.update_item(
            TableName=self.table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("mark_compaction_run_failed")
    def mark_compaction_run_failed(
        self,
        image_id: str,
        receipt_id: int,
        run_id: str,
        collection: str,
        error: str,
    ) -> None:
        """Mark a collection state as FAILED with error text.
        
        Uses atomic UpdateExpression to update only specific fields, preventing race conditions
        when both lines and words collection updates happen simultaneously.
        """
        pk = f"IMAGE#{image_id}"
        sk = f"RECEIPT#{receipt_id:05d}#COMPACTION_RUN#{run_id}"
        now = datetime.now(timezone.utc).isoformat()
        
        # Build UpdateExpression based on collection type
        if collection == "lines":
            update_expression = (
                "SET lines_state = :state, "
                "lines_error = :error, "
                "lines_finished_at = :finished_at, "
                "updated_at = :now"
            )
            expression_attribute_values = {
                ":state": {"S": "FAILED"},
                ":error": {"S": error},
                ":finished_at": {"S": now},
                ":now": {"S": now},
            }
        else:  # words
            update_expression = (
                "SET words_state = :state, "
                "words_error = :error, "
                "words_finished_at = :finished_at, "
                "updated_at = :now"
            )
            expression_attribute_values = {
                ":state": {"S": "FAILED"},
                ":error": {"S": error},
                ":finished_at": {"S": now},
                ":now": {"S": now},
            }
        
        # Atomic update - only modifies fields for this collection
        self._client.update_item(
            TableName=self.table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

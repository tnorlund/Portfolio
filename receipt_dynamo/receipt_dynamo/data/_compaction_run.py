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
        """Mark a collection state as COMPLETED and set finished_at + merged count."""
        run = self.get_compaction_run(image_id, receipt_id, run_id)
        if run is None:
            raise EntityNotFoundError("CompactionRun not found")
        now = datetime.now(timezone.utc).isoformat()
        if collection == "lines":
            run.lines_state = "COMPLETED"
            run.lines_finished_at = now
            run.lines_merged_vectors = merged_vectors
        else:
            run.words_state = "COMPLETED"
            run.words_finished_at = now
            run.words_merged_vectors = merged_vectors
        run.updated_at = now
        self.update_compaction_run(run)

    @handle_dynamodb_errors("mark_compaction_run_failed")
    def mark_compaction_run_failed(
        self,
        image_id: str,
        receipt_id: int,
        run_id: str,
        collection: str,
        error: str,
    ) -> None:
        """Mark a collection state as FAILED with error text."""
        run = self.get_compaction_run(image_id, receipt_id, run_id)
        if run is None:
            raise EntityNotFoundError("CompactionRun not found")
        now = datetime.now(timezone.utc).isoformat()
        if collection == "lines":
            run.lines_state = "FAILED"
            run.lines_error = error
            run.lines_finished_at = now
        else:
            run.words_state = "FAILED"
            run.words_error = error
            run.words_finished_at = now
        run.updated_at = now
        self.update_compaction_run(run)

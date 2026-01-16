from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.constants import CoreMLExportStatus
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.coreml_export_job import (
    CoreMLExportJob,
    item_to_coreml_export_job,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _CoreMLExportJob(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    @handle_dynamodb_errors("add_coreml_export_job")
    def add_coreml_export_job(self, export_job: CoreMLExportJob) -> None:
        """Adds a CoreML export job to the database.

        Args:
            export_job: The CoreML export job to add.

        Raises:
            EntityAlreadyExistsError: When an export job with same ID exists.
            EntityValidationError: If export_job parameters are invalid.
        """
        self._validate_entity(export_job, CoreMLExportJob, "export_job")
        self._add_entity(
            export_job,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_coreml_export_jobs")
    def add_coreml_export_jobs(
        self, export_jobs: list[CoreMLExportJob]
    ) -> None:
        """Adds a list of CoreML export jobs to the database.

        Args:
            export_jobs: The list of export jobs to add.

        Raises:
            EntityValidationError: If export_jobs parameters are invalid.
        """
        self._validate_entity_list(export_jobs, CoreMLExportJob, "export_jobs")
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=job.to_item())
            )
            for job in export_jobs
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_coreml_export_job")
    def update_coreml_export_job(self, export_job: CoreMLExportJob) -> None:
        """Updates a CoreML export job in the database.

        Args:
            export_job: The export job to update.

        Raises:
            EntityNotFoundError: If the export job does not exist.
            EntityValidationError: If export_job parameters are invalid.
        """
        self._validate_entity(export_job, CoreMLExportJob, "export_job")
        self._update_entity(
            export_job,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_coreml_export_job")
    def get_coreml_export_job(self, export_id: str) -> CoreMLExportJob:
        """Gets a CoreML export job from the database.

        Args:
            export_id: The export job ID.

        Returns:
            The CoreML export job.

        Raises:
            EntityNotFoundError: When the export job is not found.
        """
        assert_valid_uuid(export_id)

        result = self._get_entity(
            primary_key=f"COREML_EXPORT#{export_id}",
            sort_key="EXPORT",
            entity_class=CoreMLExportJob,
            converter_func=item_to_coreml_export_job,
        )

        if result is None:
            raise EntityNotFoundError(
                f"CoreML export job with export_id={export_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("delete_coreml_export_job")
    def delete_coreml_export_job(self, export_job: CoreMLExportJob) -> None:
        """Deletes a CoreML export job from the database.

        Args:
            export_job: The export job to delete.

        Raises:
            EntityNotFoundError: If the export job does not exist.
            EntityValidationError: If export_job parameters are invalid.
        """
        self._validate_entity(export_job, CoreMLExportJob, "export_job")
        self._delete_entity(
            export_job, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("list_coreml_export_jobs")
    def list_coreml_export_jobs(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[CoreMLExportJob], dict | None]:
        """Lists all CoreML export jobs from the database.

        Args:
            limit: Maximum number of export jobs to return.
            last_evaluated_key: Last evaluated key from previous query.

        Returns:
            Tuple containing list of export jobs and the last evaluated key.
        """
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )

        return self._query_by_type(
            entity_type="COREML_EXPORT",
            converter_func=item_to_coreml_export_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_coreml_export_jobs_by_status")
    def get_coreml_export_jobs_by_status(
        self,
        status: CoreMLExportStatus,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[CoreMLExportJob], dict | None]:
        """Gets CoreML export jobs by status from the database.

        Args:
            status: The status of export jobs to get.
            limit: Maximum number of export jobs to return.
            last_evaluated_key: Last evaluated key from previous query.

        Returns:
            Tuple containing list of export jobs and the last evaluated key.
        """
        if status is None:
            raise EntityValidationError("status cannot be None")
        if not isinstance(status, CoreMLExportStatus):
            raise EntityValidationError(
                "Status must be a CoreMLExportStatus instance."
            )
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "GSI2PK"},
            expression_attribute_values={
                ":val": {"S": f"COREML_EXPORT_STATUS#{status.value}"}
            },
            converter_func=item_to_coreml_export_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_coreml_export_jobs_for_job")
    def get_coreml_export_jobs_for_job(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[CoreMLExportJob], dict | None]:
        """Gets all CoreML export jobs for a specific training job.

        Args:
            job_id: The training job ID.
            limit: Maximum number of export jobs to return.
            last_evaluated_key: Last evaluated key from previous query.

        Returns:
            Tuple containing list of export jobs and the last evaluated key.
        """
        assert_valid_uuid(job_id)

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "GSI1PK"},
            expression_attribute_values={":val": {"S": f"JOB#{job_id}"}},
            converter_func=item_to_coreml_export_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

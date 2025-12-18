from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.ocr_job import OCRJob, item_to_ocr_job
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


class _OCRJob(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    @handle_dynamodb_errors("add_ocr_job")
    def add_ocr_job(self, ocr_job: OCRJob):
        """Adds an OCR job to the database

        Args:
            ocr_job (OCRJob): The OCR job to add to the database

        Raises:
            EntityAlreadyExistsError: When a OCR job with the same ID
                already exists
            EntityValidationError: If ocr_job parameters are invalid
        """
        self._validate_entity(ocr_job, OCRJob, "ocr_job")
        self._add_entity(
            ocr_job,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_ocr_jobs")
    def add_ocr_jobs(self, ocr_jobs: list[OCRJob]):
        """Adds a list of OCR jobs to the database

        Args:
            ocr_jobs (list[OCRJob]): The list of OCR jobs to add to the
                database

        Raises:
            EntityValidationError: If ocr_jobs parameters are invalid
        """
        self._validate_entity_list(ocr_jobs, OCRJob, "ocr_jobs")
        # Create write request items for batch operation
        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=job.to_item()))
            for job in ocr_jobs
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_ocr_job")
    def update_ocr_job(self, ocr_job: OCRJob):
        """Updates an OCR job in the database

        Raises:
            EntityNotFoundError: If the OCR job does not exist
            EntityValidationError: If ocr_job parameters are invalid
        """
        self._validate_entity(ocr_job, OCRJob, "ocr_job")
        self._update_entity(
            ocr_job,
            condition_expression=("attribute_exists(PK) AND attribute_exists(SK)"),
        )

    @handle_dynamodb_errors("get_ocr_job")
    def get_ocr_job(self, image_id: str, job_id: str) -> OCRJob:
        """Gets an OCR job from the database

        Args:
            image_id (str): The image ID of the OCR job
            job_id (str): The job ID of the OCR job

        Returns:
            OCRJob: The OCR job

        Raises:
            ValueError: When the OCR job is not found
        """
        self._validate_image_id(image_id)
        assert_valid_uuid(job_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"OCR_JOB#{job_id}",
            entity_class=OCRJob,
            converter_func=item_to_ocr_job,
        )

        if result is None:
            raise EntityNotFoundError(
                f"OCR job with image_id={image_id}, job_id={job_id} " "does not exist"
            )

        return result

    @handle_dynamodb_errors("delete_ocr_job")
    def delete_ocr_job(self, ocr_job: OCRJob):
        """Deletes an OCR job from the database

        Args:
            ocr_job (OCRJob): The OCR job to delete

        Raises:
            EntityNotFoundError: If the OCR job does not exist
            EntityValidationError: If ocr_job parameters are invalid
        """
        self._validate_entity(ocr_job, OCRJob, "ocr_job")
        self._delete_entity(ocr_job, condition_expression="attribute_exists(PK)")

    @handle_dynamodb_errors("delete_ocr_jobs")
    def delete_ocr_jobs(self, ocr_jobs: list[OCRJob]):
        """Deletes a list of OCR jobs from the database

        Args:
            ocr_jobs (list[OCRJob]): The list of OCR jobs to delete

        Raises:
            EntityValidationError: If ocr_jobs parameters are invalid
        """
        self._validate_entity_list(ocr_jobs, OCRJob, "ocr_jobs")
        # Create transactional delete items
        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=job.key,
                    ConditionExpression=(
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                )
            )
            for job in ocr_jobs
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("list_ocr_jobs")
    def list_ocr_jobs(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[OCRJob], dict | None]:
        """Lists all OCR jobs from the database

        Args:
            limit (int, optional): The maximum number of OCR jobs to return.
                Defaults to None.
            last_evaluated_key (dict | None, optional): The last evaluated key
                from the previous query. Defaults to None.

        Returns:
            tuple[list[OCRJob], dict | None]: A tuple containing a list of OCR
                jobs and the last evaluated key
        """
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")

        return self._query_by_type(
            entity_type="OCR_JOB",
            converter_func=item_to_ocr_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_ocr_jobs_by_status")
    def get_ocr_jobs_by_status(
        self,
        status: OCRStatus,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[OCRJob], dict | None]:
        """Gets OCR jobs by status from the database

        Args:
            status (OCRStatus): The status of the OCR jobs to get
            limit (int, optional): The maximum number of OCR jobs to return.
                Defaults to None.
            last_evaluated_key (dict | None, optional): The last evaluated key
                from the previous query. Defaults to None.

        Returns:
            tuple[list[OCRJob], dict | None]: A tuple containing a list of OCR
                jobs and the last evaluated key
        """
        if status is None:
            raise EntityValidationError("status cannot be None")
        if not isinstance(status, OCRStatus):
            raise EntityValidationError("Status must be a OCRStatus instance.")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "GSI1PK"},
            expression_attribute_values={
                ":val": {"S": f"OCR_JOB_STATUS#{status.value}"}
            },
            converter_func=item_to_ocr_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

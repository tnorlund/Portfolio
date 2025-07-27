from typing import TYPE_CHECKING, Any, Dict, List, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteTypeDef,
        PutRequestTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteTypeDef,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.entities.ocr_job import OCRJob, item_to_ocr_job
from receipt_dynamo.entities.util import assert_valid_uuid
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)


class _OCRJob(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    @handle_dynamodb_errors("add_ocr_job")
    def add_ocr_job(self, ocr_job: OCRJob):
        """Adds an OCR job to the database

        Args:
            ocr_job (OCRJob): The OCR job to add to the database

        Raises:
            EntityAlreadyExistsError: When a OCR job with the same ID already exists
            EntityValidationError: If ocr_job parameters are invalid
        """
        self._validate_entity(ocr_job, OCRJob, "ocr_job")
        self._add_entity(
            ocr_job,
            condition_expression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
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
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=job.to_item())
            )
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
            condition_expression="attribute_exists(PK) AND attribute_exists(SK)"
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
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(image_id)
        assert_valid_uuid(job_id)
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"OCR_JOB#{job_id}"},
                },
            )
            if "Item" in response:
                return item_to_ocr_job(response["Item"])
            else:
                raise ValueError(
                    f"OCR job for Image ID '{image_id}' and Job ID '{job_id}' "
                    "does not exist."
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise RuntimeError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise RuntimeError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise RuntimeError(f"Access denied: {e}") from e
            else:
                raise RuntimeError(f"Error getting OCR job: {e}") from e

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
        self._delete_entity(
            ocr_job,
            condition_expression="attribute_exists(PK)"
        )

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
                    ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)"
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
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")

        jobs: List[OCRJob] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "OCR_JOB"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend(
                    [item_to_ocr_job(item) for item in response["Items"]]
                )

                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return jobs, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise RuntimeError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise RuntimeError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise RuntimeError(f"Access denied: {e}") from e
            else:
                raise RuntimeError(f"Error listing OCR jobs: {e}") from e

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
            raise ValueError("status cannot be None")
        if not isinstance(status, OCRStatus):
            raise ValueError("Status must be a OCRStatus instance.")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")

        jobs: List[OCRJob] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "GSI1PK"},
                "ExpressionAttributeValues": {
                    ":val": {"S": f"OCR_JOB_STATUS#{status.value}"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend(
                    [item_to_ocr_job(item) for item in response["Items"]]
                )

                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return jobs, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise RuntimeError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise RuntimeError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise RuntimeError(f"Access denied: {e}") from e
            else:
                raise RuntimeError(
                    f"Error getting OCR jobs by status: {e}"
                ) from e

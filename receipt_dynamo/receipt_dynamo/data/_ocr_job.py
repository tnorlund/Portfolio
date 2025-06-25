from typing import Optional

from botocore.exceptions import ClientError
from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.ocr_job import OCRJob, itemToOCRJob
from receipt_dynamo.entities.util import assert_valid_uuid


class _OCRJob(DynamoClientProtocol):
    def addOCRJob(self, ocr_job: OCRJob):
        """Adds an OCR job to the database

        Args:
            ocr_job (OCRJob): The OCR job to add to the database

        Raises:
            ValueError: When a OCR job with the same ID already exists
        """
        if ocr_job is None:
            raise ValueError(
                "ocr_job parameter is required and cannot be None."
            )
        if not isinstance(ocr_job, OCRJob):
            raise ValueError(
                "ocr_job must be an instance of the OCRJob class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=ocr_job.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"OCR job for Image ID '{ocr_job.image_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise RuntimeError(
                    f"Could not add OCR job to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise RuntimeError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise RuntimeError(f"Internal server error: {e}") from e
            else:
                raise RuntimeError(
                    f"Could not add OCR job to DynamoDB: {e}"
                ) from e

    def addOCRJobs(self, ocr_jobs: list[OCRJob]):
        """Adds a list of OCR jobs to the database

        Args:
            ocr_jobs (list[OCRJob]): The list of OCR jobs to add to the database

        Raises:
            ValueError: When a OCR job with the same ID already exists
        """
        if ocr_jobs is None:
            raise ValueError(
                "ocr_jobs parameter is required and cannot be None."
            )
        if not isinstance(ocr_jobs, list):
            raise ValueError("ocr_jobs must be a list of OCRJob instances.")
        if not all(isinstance(job, OCRJob) for job in ocr_jobs):
            raise ValueError(
                "All OCR jobs must be instances of the OCRJob class."
            )
        for i in range(0, len(ocr_jobs), 25):
            chunk = ocr_jobs[i : i + 25]
            request_items = [
                {"PutRequest": {"Item": job.to_item()}} for job in chunk
            ]
            try:
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ProvisionedThroughputExceededException":
                    raise RuntimeError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise RuntimeError(f"Internal server error: {e}") from e
            unprocessed = response.get("UnprocessedItems", {})
            while unprocessed.get(self.table_name):
                try:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code == "ProvisionedThroughputExceededException":
                        raise RuntimeError(
                            f"Provisioned throughput exceeded: {e}"
                        ) from e
                    elif error_code == "InternalServerError":
                        raise RuntimeError(
                            f"Internal server error: {e}"
                        ) from e
                    else:
                        raise RuntimeError(
                            f"Could not add OCR jobs to DynamoDB: {e}"
                        ) from e

    def updateOCRJob(self, ocr_job: OCRJob):
        """Updates an OCR job in the database"""
        if ocr_job is None:
            raise ValueError("OCR job is required and cannot be None.")
        if not isinstance(ocr_job, OCRJob):
            raise ValueError(
                "OCR job must be an instance of the OCRJob class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=ocr_job.to_item(),
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
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
                raise RuntimeError(f"Error updating OCR job: {e}") from e

    def getOCRJob(self, image_id: str, job_id: str) -> OCRJob:
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
            raise ValueError("Image ID is required and cannot be None.")
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
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
                return itemToOCRJob(response["Item"])
            else:
                raise ValueError(
                    f"OCR job for Image ID '{image_id}' and Job ID '{job_id}' does not exist."
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

    def deleteOCRJob(self, ocr_job: OCRJob):
        """Deletes an OCR job from the database

        Args:
            image_id (str): The image ID of the OCR job
            job_id (str): The job ID of the OCR job
        """
        if ocr_job is None:
            raise ValueError("OCR job is required and cannot be None.")
        if not isinstance(ocr_job, OCRJob):
            raise ValueError(
                "OCR job must be an instance of the OCRJob class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{ocr_job.image_id}"},
                    "SK": {"S": f"OCR_JOB#{ocr_job.job_id}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"OCR job for Image ID '{ocr_job.image_id}' and Job ID '{ocr_job.job_id}' does not exist."
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise RuntimeError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise RuntimeError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise RuntimeError(f"Access denied: {e}") from e
            else:
                raise RuntimeError(f"Error deleting OCR job: {e}") from e

    def deleteOCRJobs(self, ocr_jobs: list[OCRJob]):
        """Deletes a list of OCR jobs from the database

        Args:
            ocr_jobs (list[OCRJob]): The list of OCR jobs to delete
        """
        if ocr_jobs is None:
            raise ValueError("ocr_jobs is required and cannot be None.")
        if not isinstance(ocr_jobs, list):
            raise ValueError("ocr_jobs must be a list of OCRJob instances.")
        if not all(isinstance(job, OCRJob) for job in ocr_jobs):
            raise ValueError(
                "All ocr_jobs must be instances of the OCRJob class."
            )
        for i in range(0, len(ocr_jobs), 25):
            chunk = ocr_jobs[i : i + 25]
            transact_items = []
            for item in chunk:
                transact_items.append(
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": item.key(),
                            "ConditionExpression": "attribute_exists(PK) AND attribute_exists(SK)",
                        }
                    }
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError("OCR job does not exist") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise RuntimeError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise RuntimeError(f"Internal server error: {e}") from e
                elif error_code == "AccessDeniedException":
                    raise RuntimeError(f"Access denied: {e}") from e
                else:
                    raise RuntimeError(f"Error deleting OCR jobs: {e}") from e

    def listOCRJobs(
        self,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[dict] = None,
    ) -> tuple[list[OCRJob], dict | None]:
        """Lists all OCR jobs from the database

        Args:
            limit (int, optional): The maximum number of OCR jobs to return. Defaults to None.
            lastEvaluatedKey (dict | None, optional): The last evaluated key from the previous query. Defaults to None.

        Returns:
            tuple[list[OCRJob], dict | None]: A tuple containing a list of OCR jobs and the last evaluated key
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")

        jobs = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "OCR_JOB"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend([itemToOCRJob(item) for item in response["Items"]])

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

    def getOCRJobsByStatus(
        self,
        status: OCRStatus,
        limit: Optional[int] = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[OCRJob], dict | None]:
        """Gets OCR jobs by status from the database

        Args:
            status (OCRStatus): The status of the OCR jobs to get
            limit (int, optional): The maximum number of OCR jobs to return. Defaults to None.
            lastEvaluatedKey (dict | None, optional): The last evaluated key from the previous query. Defaults to None.

        Returns:
            tuple[list[OCRJob], dict | None]: A tuple containing a list of OCR jobs and the last evaluated key
        """
        if status is None:
            raise ValueError("Status is required and cannot be None.")
        if not isinstance(status, OCRStatus):
            raise ValueError("Status must be a OCRStatus instance.")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")

        jobs = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "GSI1PK"},
                "ExpressionAttributeValues": {
                    ":val": {"S": f"OCR_JOB_STATUS#{status.value}"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend([itemToOCRJob(item) for item in response["Items"]])

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

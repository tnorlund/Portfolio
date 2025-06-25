from typing import Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.job_resource import JobResource, itemToJobResource
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: dict) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(f"LastEvaluatedKey must contain keys: {required_keys}")
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _JobResource(DynamoClientProtocol):
    def addJobResource(self, job_resource: JobResource):
        """Adds a job resource to the database

        Args:
            job_resource (JobResource): The job resource to add to the database

        Raises:
            ValueError: When a job resource with the same resource ID already exists
        """
        if job_resource is None:
            raise ValueError("JobResource parameter is required and cannot be None.")
        if not isinstance(job_resource, JobResource):
            raise ValueError(
                "job_resource must be an instance of the JobResource class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_resource.to_item(),
                ConditionExpression="attribute_not_exists(PK) OR attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"JobResource with resource ID {job_resource.resource_id} for job {job_resource.job_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add job resource to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Could not add job resource to DynamoDB: {e}") from e

    def getJobResource(self, job_id: str, resource_id: str) -> JobResource:
        """Gets a specific job resource by job ID and resource ID

        Args:
            job_id (str): The ID of the job
            resource_id (str): The ID of the resource

        Returns:
            JobResource: The requested job resource

        Raises:
            ValueError: If the job resource does not exist
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)
        if not resource_id or not isinstance(resource_id, str):
            raise ValueError("Resource ID is required and must be a non-empty string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"RESOURCE#{resource_id}"},
                },
            )

            if "Item" not in response:
                raise ValueError(
                    f"No job resource found with job ID {job_id} and resource ID {resource_id}"
                )

            return itemToJobResource(response["Item"])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get job resource: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting job resource: {e}") from e

    def updateJobResourceStatus(
        self,
        job_id: str,
        resource_id: str,
        status: str,
        released_at: Optional[str] = None,
    ):
        """Updates the status of a job resource

        Args:
            job_id (str): The ID of the job
            resource_id (str): The ID of the resource
            status (str): The new status of the resource
            released_at (str, optional): The timestamp when the resource was released (required for 'released' status)

        Raises:
            ValueError: If the job resource does not exist or parameters are invalid
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)
        if not resource_id or not isinstance(resource_id, str):
            raise ValueError("Resource ID is required and must be a non-empty string.")
        if not status or not isinstance(status, str):
            raise ValueError("Status is required and must be a non-empty string.")

        valid_statuses = ["allocated", "released", "failed", "pending"]
        if status.lower() not in valid_statuses:
            raise ValueError(f"Invalid status. Must be one of {valid_statuses}")

        if status.lower() == "released" and not released_at:
            raise ValueError(
                "released_at timestamp is required when status is 'released'"
            )

        try:
            update_expression = "SET #status = :status"
            expression_attribute_names = {"#status": "status"}
            expression_attribute_values = {":status": {"S": status}}

            if released_at:
                update_expression += ", released_at = :released_at"
                expression_attribute_values[":released_at"] = {"S": released_at}

            self._client.update_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"RESOURCE#{resource_id}"},
                },
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"No job resource found with job ID {job_id} and resource ID {resource_id}"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not update job resource status: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error updating job resource status: {e}") from e

    def listJobResources(
        self,
        job_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve resources for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get resources for.
            limit (int, optional): The maximum number of resources to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobResource objects for the specified job.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        resources = []
        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "RESOURCE#"},
                },
                "ScanIndexForward": True,  # Ascending order by default
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(resources)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_RESOURCE":
                        resources.append(itemToJobResource(item))

                if limit is not None and len(resources) >= limit:
                    resources = resources[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return resources, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list job resources from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not list job resources from the database: {e}"
                ) from e

    def listResourcesByType(
        self,
        resource_type: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve all resources of a specific type across all jobs.

        Parameters:
            resource_type (str): The type of resource to search for.
            limit (int, optional): The maximum number of resources to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobResource objects with the specified type.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not resource_type or not isinstance(resource_type, str):
            raise ValueError(
                "Resource type is required and must be a non-empty string."
            )

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        resources = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": "RESOURCE"},
                    ":rt": {"S": resource_type},
                },
                "FilterExpression": "resource_type = :rt",
                "ScanIndexForward": True,  # Ascending order by default
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(resources)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_RESOURCE":
                        resources.append(itemToJobResource(item))

                if limit is not None and len(resources) >= limit:
                    resources = resources[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return resources, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not query resources by type from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not query resources by type from the database: {e}"
                ) from e

    def getResourceById(
        self, resource_id: str
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve a specific resource by its ID (may be attached to multiple jobs).

        Parameters:
            resource_id (str): The ID of the resource to search for.

        Returns:
            tuple:
                - A list of JobResource objects with the specified resource ID.
                - A dict representing the LastEvaluatedKey from the query, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not resource_id or not isinstance(resource_id, str):
            raise ValueError("Resource ID is required and must be a non-empty string.")

        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression="GSI1PK = :pk AND GSI1SK = :sk",
                ExpressionAttributeValues={
                    ":pk": {"S": "RESOURCE"},
                    ":sk": {"S": f"RESOURCE#{resource_id}"},
                },
            )

            resources = []
            for item in response["Items"]:
                if item.get("TYPE", {}).get("S") == "JOB_RESOURCE":
                    resources.append(itemToJobResource(item))

            last_evaluated_key = response.get("LastEvaluatedKey", None)
            return resources, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get resource by ID: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting resource by ID: {e}") from e

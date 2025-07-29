from typing import TYPE_CHECKING, Any, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
    ReceiptDynamoError,
)
from receipt_dynamo.entities.job_resource import (
    JobResource,
    item_to_job_resource,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise EntityValidationError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
            )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _JobResource(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
):
    @handle_dynamodb_errors("add_job_resource")
    def add_job_resource(self, job_resource: JobResource):
        """Adds a job resource to the database

        Args:
            job_resource (JobResource): The job resource to add to the database

        Raises:
            ValueError: When a job resource with the same resource ID
                already exists
        """
        self._validate_entity(job_resource, JobResource, "job_resource")
        self._add_entity(
            job_resource,
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            )
        )

    @handle_dynamodb_errors("get_job_resource")
    def get_job_resource(self, job_id: str, resource_id: str) -> JobResource:
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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not resource_id or not isinstance(resource_id, str):
            raise EntityValidationError(
                "Resource ID is required and must be a non-empty string."
            )

        result = self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key=f"RESOURCE#{resource_id}",
            entity_class=JobResource,
            converter_func=item_to_job_resource
        )
        
        if result is None:
            raise EntityNotFoundError(
                (
                    "No job resource found with job ID "
                    f"{job_id} and resource ID {resource_id}"
            )
            )
        
        return result

    def update_job_resource_status(
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
            released_at (str, optional): The timestamp when the resource was
                released (required for 'released' status)

        Raises:
            ValueError: If the job resource does not exist or parameters are
                invalid
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not resource_id or not isinstance(resource_id, str):
            raise EntityValidationError(
                "Resource ID is required and must be a non-empty string."
            )
        if not status or not isinstance(status, str):
            raise EntityValidationError(
                "Status is required and must be a non-empty string."
            )

        valid_statuses = ["allocated", "released", "failed", "pending"]
        if status.lower() not in valid_statuses:
            raise EntityValidationError(
                f"Invalid status. Must be one of {valid_statuses}"
            )

        if status.lower() == "released" and not released_at:
            raise EntityValidationError(
                "released_at timestamp is required when status is 'released'"
            )

        try:
            update_expression = "SET #status = :status"
            expression_attribute_names = {"#status": "status"}
            expression_attribute_values = {":status": {"S": status}}

            if released_at:
                update_expression += ", released_at = :released_at"
                expression_attribute_values[":released_at"] = {
                    "S": released_at
                }

            self._client.update_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"RESOURCE#{resource_id}"},
                },
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
                ConditionExpression=(
                    "attribute_exists(PK) AND attribute_exists(SK)"
                ),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise EntityNotFoundError(
                    (
                        "No job resource found with job ID "
                        f"{job_id} and resource ID {resource_id}"
            )
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise ReceiptDynamoError(
                    f"Could not update job resource status: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error updating job resource status: {e}"
                ) from e

    @handle_dynamodb_errors("list_job_resources")
    def list_job_resources(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve resources for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get resources for.
            limit (int, optional): The maximum number of resources to return.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query.

        Returns:
            tuple:
                - A list of JobResource objects for the specified job.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "RESOURCE#"},
            },
            converter_func=item_to_job_resource,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True  # Ascending order by default
        )

    @handle_dynamodb_errors("list_resources_by_type")
    def list_resources_by_type(
        self,
        resource_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve all resources of a specific type across all jobs.

        Parameters:
            resource_type (str): The type of resource to search for.
            limit (int, optional): The maximum number of resources to return.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query.

        Returns:
            tuple:
                - A list of JobResource objects with the specified type.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not resource_type or not isinstance(resource_type, str):
            raise EntityValidationError(
                "Resource type is required and must be a non-empty string."
            )

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": "RESOURCE"},
                ":rt": {"S": resource_type},
            },
            converter_func=item_to_job_resource,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True,  # Ascending order by default
            filter_expression="resource_type = :rt"
        )

    @handle_dynamodb_errors("get_resource_by_id")
    def get_resource_by_id(
        self, resource_id: str
    ) -> tuple[list[JobResource], dict | None]:
        """
        Retrieve a specific resource by its ID (may be attached to multiple
            jobs).

        Parameters:
            resource_id (str): The ID of the resource to search for.

        Returns:
            tuple:
                - A list of JobResource objects with the specified resource ID.
                - A dict representing the LastEvaluatedKey from the query, or
                    None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not resource_id or not isinstance(resource_id, str):
            raise EntityValidationError(
                "Resource ID is required and must be a non-empty string."
            )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk AND GSI1SK = :sk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": "RESOURCE"},
                ":sk": {"S": f"RESOURCE#{resource_id}"},
            },
            converter_func=item_to_job_resource,
            limit=None,
            last_evaluated_key=None
        )

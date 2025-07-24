from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import botocore
from botocore.exceptions import ClientError

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
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    PutRequestTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data._job import validate_last_evaluated_key
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
from receipt_dynamo.entities.instance import Instance, item_to_instance
from receipt_dynamo.entities.instance_job import (
    InstanceJob,
    item_to_instance_job,
)


class _Instance(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """Class for interacting with instance-related data in DynamoDB."""

    @handle_dynamodb_errors("add_instance")
    def add_instance(self, instance: Instance) -> None:
        """Adds a new instance to the DynamoDB table.

        Args:
            instance (Instance): The instance to add.

        Raises:
            EntityAlreadyExistsError: If the instance already exists.
            EntityValidationError: If instance parameters are invalid.
        """
        self._validate_entity(instance, Instance, "instance")
        self._add_entity(
            instance,
            condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_instances")
    def add_instances(self, instances: List[Instance]) -> None:
        """Adds multiple instances to the DynamoDB table.

        Args:
            instances (List[Instance]): The instances to add.

        Raises:
            EntityValidationError: If instances parameters are invalid.
        """
        self._validate_entity_list(instances, Instance, "instances")
        # Create write request items for batch operation
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=instance.to_item())
            )
            for instance in instances
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_instance")
    def update_instance(self, instance: Instance) -> None:
        """Updates an existing instance in the DynamoDB table.

        Args:
            instance (Instance): The instance to update.

        Raises:
            EntityNotFoundError: If the instance does not exist.
            EntityValidationError: If instance parameters are invalid.
        """
        self._validate_entity(instance, Instance, "instance")
        self._update_entity(
            instance,
            condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_instance")
    def delete_instance(self, instance: Instance) -> None:
        """Deletes an instance from the DynamoDB table.

        Args:
            instance (Instance): The instance to delete.

        Raises:
            EntityNotFoundError: If the instance does not exist.
            EntityValidationError: If instance parameters are invalid.
        """
        self._validate_entity(instance, Instance, "instance")
        self._delete_entity(
            instance,
            condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("get_instance")
    def get_instance(self, instance_id: str) -> Instance:
        """Gets an instance from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance to get.

        Returns:
            Instance: The requested instance.

        Raises:
            ValueError:
                If the instance ID is invalid or the instance doesn't exist.
            Exception: If the request failed due to an unknown error.
        """
        if not instance_id:
            raise ValueError("instance_id cannot be None or empty")

        try:
            # Get the instance from DynamoDB
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"INSTANCE#{instance_id}"},
                    "SK": {"S": "INSTANCE"},
                },
            )

            # Check if the instance exists
            if "Item" not in response:
                raise ValueError(f"Instance {instance_id} does not exist")

            # Convert the DynamoDB item to an Instance object
            return item_to_instance(response["Item"])
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    f"Failed to get instance: {e.response['Error']['Message']}"
                ) from e

    @handle_dynamodb_errors("get_instance_with_jobs")
    def get_instance_with_jobs(
        self, instance_id: str
    ) -> Tuple[Instance, List[InstanceJob]]:
        """Gets an instance and its associated jobs from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance to get.

        Returns:
            Tuple[Instance, List[InstanceJob]]: The instance and its jobs.

        Raises:
            ValueError:
                If the instance ID is invalid or the instance doesn't exist.
            Exception: If the request failed due to an unknown error.
        """
        # First, get the instance
        instance = self.get_instance(instance_id)

        # Then, query for its jobs
        instance_jobs = self.list_instance_jobs(instance_id)[
            0
        ]  # Ignore last_evaluated_key

        return instance, instance_jobs

    @handle_dynamodb_errors("add_instance_job")
    def add_instance_job(self, instance_job: InstanceJob) -> None:
        """Adds a new instance-job association to the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to add.

        Raises:
            EntityAlreadyExistsError: If the instance-job already exists.
            EntityValidationError: If instance_job parameters are invalid.
        """
        self._validate_entity(instance_job, InstanceJob, "instance_job")
        self._add_entity(
            instance_job,
            condition_expression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
        )

    @handle_dynamodb_errors("update_instance_job")
    def update_instance_job(self, instance_job: InstanceJob) -> None:
        """Updates an existing instance-job association in the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to update.

        Raises:
            EntityNotFoundError: If the instance-job does not exist.
            EntityValidationError: If instance_job parameters are invalid.
        """
        self._validate_entity(instance_job, InstanceJob, "instance_job")
        self._update_entity(
            instance_job,
            condition_expression="attribute_exists(PK) AND attribute_exists(SK)"
        )

    @handle_dynamodb_errors("delete_instance_job")
    def delete_instance_job(self, instance_job: InstanceJob) -> None:
        """Deletes an instance-job association from the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to delete.

        Raises:
            EntityNotFoundError: If the instance-job does not exist.
            EntityValidationError: If instance_job parameters are invalid.
        """
        self._validate_entity(instance_job, InstanceJob, "instance_job")
        self._delete_entity(
            instance_job,
            condition_expression="attribute_exists(PK) AND attribute_exists(SK)"
        )

    @handle_dynamodb_errors("get_instance_job")
    def get_instance_job(self, instance_id: str, job_id: str) -> InstanceJob:
        """Gets an instance-job association from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance.
            job_id (str): The ID of the job.

        Returns:
            InstanceJob: The requested instance-job association.

        Raises:
            ValueError:
                If the IDs are invalid or the instance-job doesn't exist.
            Exception: If the request failed due to an unknown error.
        """
        if not instance_id:
            raise ValueError("instance_id cannot be None or empty")
        if not job_id:
            raise ValueError("job_id cannot be None or empty")

        try:
            # Get the instance-job from DynamoDB
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"INSTANCE#{instance_id}"},
                    "SK": {"S": f"JOB#{job_id}"},
                },
            )

            # Check if the instance-job exists
            if "Item" not in response:
                raise ValueError(
                    (
                        "InstanceJob for instance "
                        f"{instance_id} and job {job_id} does not exist"
                    )
                )

            # Convert the DynamoDB item to an InstanceJob object
            return item_to_instance_job(response["Item"])
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    (
                        "Failed to get instance-job: "
                        f"{e.response['Error']['Message']}"
                    )
                ) from e

    @handle_dynamodb_errors("list_instances")
    def list_instances(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """Lists instances in the DynamoDB table.

        Args:
            limit (int, optional): The maximum number of instances to return.
            last_evaluated_key (dict, optional):
                The exclusive start key for pagination.

        Returns:
            Tuple[List[Instance], Optional[Dict]]:
                A tuple containing the list of instances and the last evaluated
                key for pagination, if any.

        Raises:
            ValueError: If the last_evaluated_key is invalid.
            Exception: If the request failed due to an unknown error.
        """
        # Validate the last_evaluated_key if provided
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "INSTANCE"}},
        }

        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            instances = []
            response = self._client.query(**query_params)
            instances = [
                item_to_instance(item) for item in response.get("Items", [])
            ]
            return instances, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    (
                        "Failed to list instances: "
                        f"{e.response['Error']['Message']}"
                    )
                ) from e

    @handle_dynamodb_errors("list_instances_by_status")
    def list_instances_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """Lists instances by status in the DynamoDB table.

        Args:
            status (str): The status to filter by.
            limit (int, optional): The maximum number of instances to return.
            last_evaluated_key (dict, optional):
                The exclusive start key for pagination.

        Returns:
            Tuple[List[Instance], Optional[Dict]]:
                A tuple containing the list of instances and the last evaluated
                key for pagination, if any.

        Raises:
            ValueError: If the status or last_evaluated_key is invalid.
            Exception: If the request failed due to an unknown error.
        """
        # Validate status
        valid_statuses = ["pending", "running", "stopped", "terminated"]
        if not status or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")

        # Validate the last_evaluated_key if provided
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :gsi1pk",
            "ExpressionAttributeValues": {
                ":gsi1pk": {"S": f"STATUS#{status.lower()}"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            response = self._client.query(**query_params)
            instances = [
                item_to_instance(item) for item in response.get("Items", [])
            ]
            return instances, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    (
                        "Failed to list instances by status: "
                        f"{e.response['Error']['Message']}"
                    )
                ) from e

    @handle_dynamodb_errors("list_instance_jobs")
    def list_instance_jobs(
        self,
        instance_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """Lists jobs associated with an instance in the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance.
            limit (int, optional): The maximum number of jobs to return.
            last_evaluated_key (dict, optional):
                The exclusive start key for pagination.

        Returns:
            Tuple[List[InstanceJob], Optional[Dict]]:
                A tuple containing the list of instance-job associations and
                the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the instance_id or last_evaluated_key is invalid.
            Exception: If the request failed due to an unknown error.
        """
        if not instance_id:
            raise ValueError("instance_id cannot be None or empty")

        # Validate the last_evaluated_key if provided
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": (
                "PK = :pk AND begins_with(SK, :sk_prefix)"
            ),
            "ExpressionAttributeValues": {
                ":pk": {"S": f"INSTANCE#{instance_id}"},
                ":sk_prefix": {"S": "JOB#"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            response = self._client.query(**query_params)
            instance_jobs = [
                item_to_instance_job(item)
                for item in response.get("Items", [])
            ]
            return instance_jobs, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    (
                        "Failed to list instance jobs: "
                        f"{e.response['Error']['Message']}"
                    )
                ) from e

    @handle_dynamodb_errors("list_instances_for_job")
    def list_instances_for_job(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """Lists instances associated with a job in the DynamoDB table.

        Args:
            job_id (str): The ID of the job.
            limit (int, optional): The maximum number of instances to return.
            last_evaluated_key (dict, optional):
                The exclusive start key for pagination.

        Returns:
            Tuple[List[InstanceJob], Optional[Dict]]:
                A tuple containing the list of instance-job associations and
                the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the job_id or last_evaluated_key is invalid.
            Exception: If the request failed due to an unknown error.
        """
        if not job_id:
            raise ValueError("job_id cannot be None or empty")

        # Validate the last_evaluated_key if provided
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": (
                "GSI1PK = :gsi1pk AND begins_with(GSI1SK, :gsi1sk_prefix)"
            ),
            "ExpressionAttributeValues": {
                ":gsi1pk": {"S": "JOB"},
                ":gsi1sk_prefix": {"S": f"JOB#{job_id}#INSTANCE#"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            response = self._client.query(**query_params)
            instance_jobs = [
                item_to_instance_job(item)
                for item in response.get("Items", [])
            ]
            return instance_jobs, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise EntityNotFoundError(
                    f"Table {self.table_name} does not exist"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(
                    "Internal server error, retry later"
                ) from e
            else:
                raise OperationError(
                    (
                        "Failed to list instances for job: "
                        f"{e.response['Error']['Message']}"
                    )
                ) from e

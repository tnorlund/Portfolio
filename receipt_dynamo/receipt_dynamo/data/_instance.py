from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

# Runtime imports needed by the class methods
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    QueryByParentMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.instance import Instance, item_to_instance
from receipt_dynamo.entities.instance_job import (
    InstanceJob,
    item_to_instance_job,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


class _Instance(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    QueryByParentMixin,
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
        self._add_entity(instance, condition_expression="attribute_not_exists(PK)")

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
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=instance.to_item()))
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
        self._update_entity(instance, condition_expression="attribute_exists(PK)")

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
        self._delete_entity(instance, condition_expression="attribute_exists(PK)")

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
            raise EntityValidationError("instance_id cannot be None or empty")

        result = self._get_entity(
            primary_key=f"INSTANCE#{instance_id}",
            sort_key="INSTANCE",
            entity_class=Instance,
            converter_func=item_to_instance,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Instance with instance id {instance_id} does not exist"
            )

        return result

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

        # Then, query for its jobs using _query_by_parent
        instance_jobs, _ = self._query_by_parent(
            parent_key_prefix=f"INSTANCE#{instance_id}",
            child_key_prefix="JOB#",
            converter_func=item_to_instance_job,
            limit=None,
            last_evaluated_key=None,
        )

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
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
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
            condition_expression=("attribute_exists(PK) AND attribute_exists(SK)"),
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
            condition_expression=("attribute_exists(PK) AND attribute_exists(SK)"),
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
            raise EntityValidationError("instance_id cannot be None or empty")
        if not job_id:
            raise EntityValidationError("job_id cannot be None or empty")

        result = self._get_entity(
            primary_key=f"INSTANCE#{instance_id}",
            sort_key=f"JOB#{job_id}",
            entity_class=InstanceJob,
            converter_func=item_to_instance_job,
        )

        if result is None:
            raise EntityNotFoundError(
                (
                    "InstanceJob for instance "
                    f"{instance_id} and job {job_id} does not exist"
                )
            )

        return result

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
        return self._query_by_type(
            entity_type="INSTANCE",
            converter_func=item_to_instance,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

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
            raise EntityValidationError(f"status must be one of {valid_statuses}")

        # Validate the last_evaluated_key if provided
        if last_evaluated_key is not None:
            self._validate_pagination_params(limit, last_evaluated_key)

        # Query instances by status using GSI1
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :gsi1pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":gsi1pk": {"S": f"STATUS#{status.lower()}"},
            },
            converter_func=item_to_instance,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

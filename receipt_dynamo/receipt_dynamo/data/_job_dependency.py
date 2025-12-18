from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    SingleEntityCRUDMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.job_dependency import (
    JobDependency,
    item_to_job_dependency,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


class _JobDependency(
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
):
    """
    Provides methods for accessing job dependency data in DynamoDB.

    This class offers methods to add, get, list, and delete job dependencies.
    Methods
    -------
    add_job_dependency(job_dependency: JobDependency)
        Adds a job dependency to the database.
    get_job_dependency(dependent_job_id: str, dependency_job_id: str) ->
        JobDependency
        Gets a job dependency from the database.
    list_job_dependencies(dependent_job_id: str) -> List[JobDependency]
        Lists all dependencies for a specific job.
    delete_job_dependency(job_dependency: JobDependency)
        Deletes a job dependency from the database.
    delete_job_dependencies(job_dependencies: List[JobDependency])
        Deletes multiple job dependencies from the database.
    """

    @handle_dynamodb_errors("add_job_dependency")
    def add_job_dependency(self, job_dependency: JobDependency):
        """Adds a job dependency to the DynamoDB table.

        Args:
            job_dependency (JobDependency): The job dependency to add.

        Raises:
            ValueError: If job_dependency is None or not a JobDependency
                instance.
            ClientError: If a DynamoDB error occurs.
        """
        self._validate_entity(job_dependency, JobDependency, "job_dependency")
        self._add_entity(
            job_dependency,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_job_dependency")
    def get_job_dependency(
        self, dependent_job_id: str, dependency_job_id: str
    ) -> JobDependency:
        """Gets a job dependency from the DynamoDB table.

        Args:
            dependent_job_id (str): The ID of the job that depends on another.
            dependency_job_id (str): The ID of the job that is depended on.

        Returns:
            JobDependency: The job dependency from the DynamoDB table.

        Raises:
            ValueError: If any parameter is None, or if the dependency is not
                found.
            ClientError: If a DynamoDB error occurs.
        """
        if dependent_job_id is None:
            raise EntityValidationError("dependent_job_id cannot be None")
        if dependency_job_id is None:
            raise EntityValidationError("dependency_job_id cannot be None")

        result = self._get_entity(
            primary_key=f"JOB#{dependent_job_id}",
            sort_key=f"DEPENDS_ON#{dependency_job_id}",
            entity_class=JobDependency,
            converter_func=item_to_job_dependency,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Dependency between {dependent_job_id} and "
                f"{dependency_job_id} not found"
            )

        return result

    @handle_dynamodb_errors("list_dependencies")
    def list_dependencies(
        self,
        dependent_job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[JobDependency], Optional[Dict]]:
        """Lists all dependencies for a specific job.

        Args:
            dependent_job_id (str): The ID of the job to list dependencies for.
            limit (int, optional): The maximum number of items to return.
            last_evaluated_key (Dict, optional): The key to start pagination
                from.

        Returns:
            Tuple[List[JobDependency], Optional[Dict]]: A tuple containing the
                list of job dependencies and the last evaluated key.

        Raises:
            ValueError: If dependent_job_id is None.
            ClientError: If a DynamoDB error occurs.
        """
        if dependent_job_id is None:
            raise EntityValidationError("dependent_job_id cannot be None")

        return self._query_entities(
            index_name=None,
            key_condition_expression=("PK = :pk AND begins_with(SK, :sk_prefix)"),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{dependent_job_id}"},
                ":sk_prefix": {"S": "DEPENDS_ON#"},
            },
            converter_func=item_to_job_dependency,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_dependents")
    def list_dependents(
        self,
        dependency_job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[JobDependency], Optional[Dict]]:
        """Lists all jobs that depend on a specific job.

        Args:
            dependency_job_id (str): The ID of the job that others depend on.
            limit (int, optional): The maximum number of items to return.
            last_evaluated_key (Dict, optional): The key to start pagination
                from.

        Returns:
            Tuple[List[JobDependency], Optional[Dict]]: A tuple containing the
                list of job dependencies and the last evaluated key.

        Raises:
            ValueError: If dependency_job_id is None.
            ClientError: If a DynamoDB error occurs.
        """
        if dependency_job_id is None:
            raise EntityValidationError("dependency_job_id cannot be None")

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression=(
                "GSI2PK = :pk AND begins_with(GSI2SK, :sk_prefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": "DEPENDENCY"},
                ":sk_prefix": {"S": f"DEPENDED_BY#{dependency_job_id}#DEPENDENT#"},
            },
            converter_func=item_to_job_dependency,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("delete_job_dependency")
    def delete_job_dependency(self, job_dependency: JobDependency):
        """Deletes a job dependency from the DynamoDB table.

        Args:
            job_dependency (JobDependency): The job dependency to delete.

        Raises:
            ValueError: If job_dependency is None or not a JobDependency
                instance.
            ClientError: If a DynamoDB error occurs.
        """
        self._validate_entity(job_dependency, JobDependency, "job_dependency")
        self._delete_entity(job_dependency)

    @handle_dynamodb_errors("delete_all_dependencies")
    def delete_all_dependencies(self, dependent_job_id: str):
        """Deletes all dependencies for a specific job.

        Args:
            dependent_job_id (str): The ID of the job to delete dependencies
                for.

        Raises:
            ValueError: If dependent_job_id is None.
            ClientError: If a DynamoDB error occurs.
        """
        if dependent_job_id is None:
            raise EntityValidationError("dependent_job_id cannot be None")

        # First, get all dependencies for the job
        dependencies, _ = self.list_dependencies(dependent_job_id)

        # If there are no dependencies, we're done
        if not dependencies:
            return

        # DynamoDB batch write has a limit of 25 items
        batch_size = 25
        for i in range(0, len(dependencies), batch_size):
            batch = dependencies[i : i + batch_size]

            request_items = {
                self.table_name: [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(
                            Key={
                                "PK": {"S": f"JOB#{dep.dependent_job_id}"},
                                "SK": {"S": f"DEPENDS_ON#{dep.dependency_job_id}"},
                            }
                        )
                    )
                    for dep in batch
                ]
            }

            # Use the batch write retry method from the mixin
            # Convert request_items to the expected format
            write_requests = request_items[self.table_name]
            self._batch_write_with_retry(write_requests)

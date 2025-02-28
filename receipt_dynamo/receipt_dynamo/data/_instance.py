from typing import Dict, List, Optional, Tuple

import botocore

from receipt_dynamo.data._job import validate_last_evaluated_key
from receipt_dynamo.data.shared_exceptions import (
    DynamoCriticalErrorException,
    DynamoRetryableException,
)
from receipt_dynamo.entities.instance import Instance, itemToInstance
from receipt_dynamo.entities.instance_job import InstanceJob, itemToInstanceJob


class _Instance:
    """Class for interacting with instance-related data in DynamoDB."""

    def addInstance(self, instance: Instance) -> None:
        """Adds a new instance to the DynamoDB table.

        Args:
            instance (Instance): The instance to add.

        Raises:
            ValueError: If the instance is invalid or already exists.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance is None:
            raise ValueError("instance cannot be None")
        if not isinstance(instance, Instance):
            raise ValueError("instance must be an instance of Instance")

        try:
            # Convert the instance to a DynamoDB item
            item = instance.to_item()

            # Add the instance to DynamoDB with a condition expression
            # to ensure it doesn't already exist
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Instance {
                        instance.instance_id} already exists"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to add instance: {e.response['Error']['Message']}"
                )

    def addInstances(self, instances: List[Instance]) -> None:
        """Adds multiple instances to the DynamoDB table.

        Args:
            instances (List[Instance]): The instances to add.

        Raises:
            ValueError: If instances is invalid.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instances is None:
            raise ValueError("instances cannot be None")
        if not isinstance(instances, list):
            raise ValueError("instances must be a list")
        if not all(isinstance(instance, Instance) for instance in instances):
            raise ValueError(
                "All elements in instances must be instances of Instance"
            )

        if not instances:
            return  # Nothing to do if the list is empty

        try:
            # Convert instances to DynamoDB items
            items = [instance.to_item() for instance in instances]

            # Batch write the items to DynamoDB
            request_items = {
                self.table_name: [
                    {"PutRequest": {"Item": item}} for item in items
                ]
            }
            response = self._client.batch_write_item(
                RequestItems=request_items
            )

            # Handle unprocessed items
            unprocessed_items = response.get("UnprocessedItems", {})
            max_retries = 3
            retry_count = 0

            while unprocessed_items and retry_count < max_retries:
                retry_count += 1
                response = self._client.batch_write_item(
                    RequestItems=unprocessed_items
                )
                unprocessed_items = response.get("UnprocessedItems", {})

            if unprocessed_items:
                raise DynamoRetryableException(
                    f"Failed to write {
                        len(
                            unprocessed_items.get(
                                self.table_name,
                                []))} items after {max_retries} retries"
                )

        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            elif e.response["Error"]["Code"] == "ValidationException":
                raise DynamoCriticalErrorException(
                    f"Validation error: {e.response['Error']['Message']}"
                )
            elif e.response["Error"]["Code"] == "AccessDeniedException":
                raise DynamoCriticalErrorException(
                    f"Access denied: {e.response['Error']['Message']}"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to add instances: {
                        e.response['Error']['Message']}"
                )

    def updateInstance(self, instance: Instance) -> None:
        """Updates an existing instance in the DynamoDB table.

        Args:
            instance (Instance): The instance to update.

        Raises:
            ValueError: If the instance is invalid or doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance is None:
            raise ValueError("instance cannot be None")
        if not isinstance(instance, Instance):
            raise ValueError("instance must be an instance of Instance")

        try:
            # Convert the instance to a DynamoDB item
            item = instance.to_item()

            # Update the instance in DynamoDB with a condition expression
            # to ensure it exists
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_exists(PK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Instance {
                        instance.instance_id} does not exist"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to update instance: {
                        e.response['Error']['Message']}"
                )

    def deleteInstance(self, instance: Instance) -> None:
        """Deletes an instance from the DynamoDB table.

        Args:
            instance (Instance): The instance to delete.

        Raises:
            ValueError: If the instance is invalid or doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance is None:
            raise ValueError("instance cannot be None")
        if not isinstance(instance, Instance):
            raise ValueError("instance must be an instance of Instance")

        try:
            # Delete the instance from DynamoDB with a condition expression
            # to ensure it exists
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"INSTANCE#{instance.instance_id}"},
                    "SK": {"S": "INSTANCE"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Instance {
                        instance.instance_id} does not exist"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to delete instance: {
                        e.response['Error']['Message']}"
                )

    def getInstance(self, instance_id: str) -> Instance:
        """Gets an instance from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance to get.

        Returns:
            Instance: The requested instance.

        Raises:
            ValueError: If the instance ID is invalid or the instance doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
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
            return itemToInstance(response["Item"])
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to get instance: {e.response['Error']['Message']}"
                )

    def getInstanceWithJobs(
        self, instance_id: str
    ) -> Tuple[Instance, List[InstanceJob]]:
        """Gets an instance and its associated jobs from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance to get.

        Returns:
            Tuple[Instance, List[InstanceJob]]: The instance and its jobs.

        Raises:
            ValueError: If the instance ID is invalid or the instance doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        # First, get the instance
        instance = self.getInstance(instance_id)

        # Then, query for its jobs
        instance_jobs = self.listInstanceJobs(instance_id)[
            0
        ]  # Ignore lastEvaluatedKey

        return instance, instance_jobs

    def addInstanceJob(self, instance_job: InstanceJob) -> None:
        """Adds a new instance-job association to the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to add.

        Raises:
            ValueError: If the instance-job is invalid or already exists.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance_job is None:
            raise ValueError("instance_job cannot be None")
        if not isinstance(instance_job, InstanceJob):
            raise ValueError("instance_job must be an instance of InstanceJob")

        try:
            # Convert the instance-job to a DynamoDB item
            item = instance_job.to_item()

            # Add the instance-job to DynamoDB with a condition expression
            # to ensure it doesn't already exist
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"InstanceJob for instance {
                        instance_job.instance_id} and job {
                        instance_job.job_id} already exists"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to add instance-job: {e.response['Error']['Message']}"
                )

    def updateInstanceJob(self, instance_job: InstanceJob) -> None:
        """Updates an existing instance-job association in the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to update.

        Raises:
            ValueError: If the instance-job is invalid or doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance_job is None:
            raise ValueError("instance_job cannot be None")
        if not isinstance(instance_job, InstanceJob):
            raise ValueError("instance_job must be an instance of InstanceJob")

        try:
            # Convert the instance-job to a DynamoDB item
            item = instance_job.to_item()

            # Update the instance-job in DynamoDB with a condition expression
            # to ensure it exists
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"InstanceJob for instance {
                        instance_job.instance_id} and job {
                        instance_job.job_id} does not exist"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to update instance-job: {e.response['Error']['Message']}"
                )

    def deleteInstanceJob(self, instance_job: InstanceJob) -> None:
        """Deletes an instance-job association from the DynamoDB table.

        Args:
            instance_job (InstanceJob): The instance-job association to delete.

        Raises:
            ValueError: If the instance-job is invalid or doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if instance_job is None:
            raise ValueError("instance_job cannot be None")
        if not isinstance(instance_job, InstanceJob):
            raise ValueError("instance_job must be an instance of InstanceJob")

        try:
            # Delete the instance-job from DynamoDB with a condition expression
            # to ensure it exists
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"INSTANCE#{instance_job.instance_id}"},
                    "SK": {"S": f"JOB#{instance_job.job_id}"},
                },
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"InstanceJob for instance {
                        instance_job.instance_id} and job {
                        instance_job.job_id} does not exist"
                )
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise DynamoRetryableException(
                    "Provisioned throughput exceeded, retry later"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to delete instance-job: {e.response['Error']['Message']}"
                )

    def getInstanceJob(self, instance_id: str, job_id: str) -> InstanceJob:
        """Gets an instance-job association from the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance.
            job_id (str): The ID of the job.

        Returns:
            InstanceJob: The requested instance-job association.

        Raises:
            ValueError: If the IDs are invalid or the instance-job doesn't exist.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
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
                    f"InstanceJob for instance {instance_id} and job {job_id} does not exist"
                )

            # Convert the DynamoDB item to an InstanceJob object
            return itemToInstanceJob(response["Item"])
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to get instance-job: {e.response['Error']['Message']}"
                )

    def listInstances(
        self, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """Lists instances in the DynamoDB table.

        Args:
            limit (int, optional): The maximum number of instances to return.
            lastEvaluatedKey (dict, optional): The exclusive start key for pagination.

        Returns:
            Tuple[List[Instance], Optional[Dict]]: A tuple containing the list of instances
                and the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the lastEvaluatedKey is invalid.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        # Validate the lastEvaluatedKey if provided
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        query_params = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "INSTANCE"}},
        }

        if limit is not None:
            query_params["Limit"] = limit

        if lastEvaluatedKey is not None:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        try:
            instances = []
            response = self._client.query(**query_params)
            instances = [
                itemToInstance(item) for item in response.get("Items", [])
            ]
            return instances, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to list instances: {
                        e.response['Error']['Message']}"
                )

    def listInstancesByStatus(
        self, status: str, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """Lists instances by status in the DynamoDB table.

        Args:
            status (str): The status to filter by.
            limit (int, optional): The maximum number of instances to return.
            lastEvaluatedKey (dict, optional): The exclusive start key for pagination.

        Returns:
            Tuple[List[Instance], Optional[Dict]]: A tuple containing the list of instances
                and the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the status or lastEvaluatedKey is invalid.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        # Validate status
        valid_statuses = ["pending", "running", "stopped", "terminated"]
        if not status or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")

        # Validate the lastEvaluatedKey if provided
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        query_params = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :gsi1pk",
            "ExpressionAttributeValues": {
                ":gsi1pk": {"S": f"STATUS#{status.lower()}"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if lastEvaluatedKey is not None:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        try:
            response = self._client.query(**query_params)
            instances = [
                itemToInstance(item) for item in response.get("Items", [])
            ]
            return instances, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to list instances by status: {
                        e.response['Error']['Message']}"
                )

    def listInstanceJobs(
        self,
        instance_id: str,
        limit: int = None,
        lastEvaluatedKey: dict = None,
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """Lists jobs associated with an instance in the DynamoDB table.

        Args:
            instance_id (str): The ID of the instance.
            limit (int, optional): The maximum number of jobs to return.
            lastEvaluatedKey (dict, optional): The exclusive start key for pagination.

        Returns:
            Tuple[List[InstanceJob], Optional[Dict]]: A tuple containing the list of instance-job
                associations and the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the instance_id or lastEvaluatedKey is invalid.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if not instance_id:
            raise ValueError("instance_id cannot be None or empty")

        # Validate the lastEvaluatedKey if provided
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        query_params = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk_prefix)",
            "ExpressionAttributeValues": {
                ":pk": {"S": f"INSTANCE#{instance_id}"},
                ":sk_prefix": {"S": "JOB#"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if lastEvaluatedKey is not None:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        try:
            response = self._client.query(**query_params)
            instance_jobs = [
                itemToInstanceJob(item) for item in response.get("Items", [])
            ]
            return instance_jobs, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to list instance jobs: {
                        e.response['Error']['Message']}"
                )

    def listInstancesForJob(
        self, job_id: str, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """Lists instances associated with a job in the DynamoDB table.

        Args:
            job_id (str): The ID of the job.
            limit (int, optional): The maximum number of instances to return.
            lastEvaluatedKey (dict, optional): The exclusive start key for pagination.

        Returns:
            Tuple[List[InstanceJob], Optional[Dict]]: A tuple containing the list of instance-job
                associations and the last evaluated key for pagination, if any.

        Raises:
            ValueError: If the job_id or lastEvaluatedKey is invalid.
            DynamoRetryableException: If the request failed due to a transient error.
            DynamoCriticalErrorException: If the request failed due to a critical error.
        """
        if not job_id:
            raise ValueError("job_id cannot be None or empty")

        # Validate the lastEvaluatedKey if provided
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        query_params = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :gsi1pk AND begins_with(GSI1SK, :gsi1sk_prefix)",
            "ExpressionAttributeValues": {
                ":gsi1pk": {"S": "JOB"},
                ":gsi1sk_prefix": {"S": f"JOB#{job_id}#INSTANCE#"},
            },
        }

        if limit is not None:
            query_params["Limit"] = limit

        if lastEvaluatedKey is not None:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        try:
            response = self._client.query(**query_params)
            instance_jobs = [
                itemToInstanceJob(item) for item in response.get("Items", [])
            ]
            return instance_jobs, response.get("LastEvaluatedKey")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise DynamoCriticalErrorException(
                    f"Table {self.table_name} does not exist"
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise DynamoRetryableException(
                    "Internal server error, retry later"
                )
            else:
                raise DynamoCriticalErrorException(
                    f"Failed to list instances for job: {
                        e.response['Error']['Message']}"
                )

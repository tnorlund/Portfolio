from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptValidationCategory, itemToReceiptValidationCategory
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptValidationCategory(DynamoClientProtocol):
    """
    A class used to access receipt validation categories in DynamoDB.

    Methods
    -------
    addReceiptValidationCategory(category: ReceiptValidationCategory)
        Adds a ReceiptValidationCategory to DynamoDB.
    addReceiptValidationCategories(categories: list[ReceiptValidationCategory])
        Adds multiple ReceiptValidationCategories to DynamoDB in batches.
    updateReceiptValidationCategory(category: ReceiptValidationCategory)
        Updates an existing ReceiptValidationCategory in the database.
    updateReceiptValidationCategories(categories: list[ReceiptValidationCategory])
        Updates multiple ReceiptValidationCategories in the database.
    deleteReceiptValidationCategory(category: ReceiptValidationCategory)
        Deletes a single ReceiptValidationCategory.
    deleteReceiptValidationCategories(categories: list[ReceiptValidationCategory])
        Deletes multiple ReceiptValidationCategories in batch.
    getReceiptValidationCategory(
        receipt_id: int,
        image_id: str,
        field_name: str
    ) -> ReceiptValidationCategory:
        Retrieves a single ReceiptValidationCategory by IDs.
    listReceiptValidationCategories(
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        Returns ReceiptValidationCategories and the last evaluated key.
    listReceiptValidationCategoriesByStatus(
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        Returns ReceiptValidationCategories with a specific status.
    """

    def addReceiptValidationCategory(self, category: ReceiptValidationCategory):
        """Adds a ReceiptValidationCategory to DynamoDB.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory to add.

        Raises:
            ValueError: If the category is None or not an instance of ReceiptValidationCategory.
            Exception: If the category cannot be added to DynamoDB.
        """
        if category is None:
            raise ValueError("category parameter is required and cannot be None.")
        if not isinstance(category, ReceiptValidationCategory):
            raise ValueError(
                "category must be an instance of the ReceiptValidationCategory class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=category.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationCategory with field {category.field_name} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add receipt validation category to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not add receipt validation category to DynamoDB: {e}"
                ) from e

    def addReceiptValidationCategories(
        self, categories: list[ReceiptValidationCategory]
    ):
        """Adds multiple ReceiptValidationCategories to DynamoDB in batches.

        Args:
            categories (list[ReceiptValidationCategory]): The ReceiptValidationCategories to add.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be added to DynamoDB.
        """
        if categories is None:
            raise ValueError("categories parameter is required and cannot be None.")
        if not isinstance(categories, list):
            raise ValueError(
                "categories must be a list of ReceiptValidationCategory instances."
            )
        if not all(isinstance(cat, ReceiptValidationCategory) for cat in categories):
            raise ValueError(
                "All categories must be instances of the ReceiptValidationCategory class."
            )
        try:
            for i in range(0, len(categories), 25):
                chunk = categories[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": cat.to_item()}} for cat in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not add ReceiptValidationCategories to the database: {e}"
                ) from e

    def updateReceiptValidationCategory(self, category: ReceiptValidationCategory):
        """Updates an existing ReceiptValidationCategory in the database.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory to update.

        Raises:
            ValueError: If the category is None or not an instance of ReceiptValidationCategory.
            Exception: If the category cannot be updated in DynamoDB.
        """
        if category is None:
            raise ValueError("category parameter is required and cannot be None.")
        if not isinstance(category, ReceiptValidationCategory):
            raise ValueError(
                "category must be an instance of the ReceiptValidationCategory class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=category.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationCategory with field {category.field_name} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not update ReceiptValidationCategory in the database: {e}"
                ) from e

    def updateReceiptValidationCategories(
        self, categories: list[ReceiptValidationCategory]
    ):
        """Updates multiple ReceiptValidationCategories in the database.

        Args:
            categories (list[ReceiptValidationCategory]): The ReceiptValidationCategories to update.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be updated in DynamoDB.
        """
        if categories is None:
            raise ValueError("categories parameter is required and cannot be None.")
        if not isinstance(categories, list):
            raise ValueError(
                "categories must be a list of ReceiptValidationCategory instances."
            )
        if not all(isinstance(cat, ReceiptValidationCategory) for cat in categories):
            raise ValueError(
                "All categories must be instances of the ReceiptValidationCategory class."
            )
        for i in range(0, len(categories), 25):
            chunk = categories[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": cat.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for cat in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptValidationCategories do not exist"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception(f"Provisioned throughput exceeded: {e}") from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise Exception(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise Exception(f"Access denied: {e}") from e
                else:
                    raise Exception(
                        f"Could not update ReceiptValidationCategories in the database: {e}"
                    ) from e

    def deleteReceiptValidationCategory(self, category: ReceiptValidationCategory):
        """Deletes a single ReceiptValidationCategory.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory to delete.

        Raises:
            ValueError: If the category is None or not an instance of ReceiptValidationCategory.
            Exception: If the category cannot be deleted from DynamoDB.
        """
        if category is None:
            raise ValueError("category parameter is required and cannot be None.")
        if not isinstance(category, ReceiptValidationCategory):
            raise ValueError(
                "category must be an instance of the ReceiptValidationCategory class."
            )
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=category.key,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptValidationCategory with field {category.field_name} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptValidationCategory from the database: {e}"
                ) from e

    def deleteReceiptValidationCategories(
        self, categories: list[ReceiptValidationCategory]
    ):
        """Deletes multiple ReceiptValidationCategories in batch.

        Args:
            categories (list[ReceiptValidationCategory]): The ReceiptValidationCategories to delete.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be deleted from DynamoDB.
        """
        if categories is None:
            raise ValueError("categories parameter is required and cannot be None.")
        if not isinstance(categories, list):
            raise ValueError(
                "categories must be a list of ReceiptValidationCategory instances."
            )
        if not all(isinstance(cat, ReceiptValidationCategory) for cat in categories):
            raise ValueError(
                "All categories must be instances of the ReceiptValidationCategory class."
            )
        try:
            for i in range(0, len(categories), 25):
                chunk = categories[i : i + 25]
                request_items = [{"DeleteRequest": {"Key": cat.key}} for cat in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptValidationCategories from the database: {e}"
                ) from e

    def getReceiptValidationCategory(
        self, receipt_id: int, image_id: str, field_name: str
    ) -> ReceiptValidationCategory:
        """Retrieves a single ReceiptValidationCategory by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            field_name (str): The field name.

        Returns:
            ReceiptValidationCategory: The retrieved ReceiptValidationCategory.

        Raises:
            ValueError: If any of the parameters are None or invalid.
            Exception: If the ReceiptValidationCategory cannot be retrieved from DynamoDB.
        """
        if receipt_id is None:
            raise ValueError("receipt_id parameter is required and cannot be None.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id parameter is required and cannot be None.")
        assert_valid_uuid(image_id)
        if field_name is None:
            raise ValueError("field_name parameter is required and cannot be None.")
        if not isinstance(field_name, str):
            raise ValueError("field_name must be a string.")
        if not field_name:
            raise ValueError("field_name must not be empty.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{field_name}"
                    },
                },
            )
            if "Item" in response:
                return itemToReceiptValidationCategory(response["Item"])
            else:
                raise ValueError(
                    f"ReceiptValidationCategory with field {field_name} not found"
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Error getting receipt validation category: {e}"
                ) from e

    def listReceiptValidationCategories(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        """Returns all ReceiptValidationCategories from the table using GSI1.

        Args:
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation categories cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple containing a list of validation categories and
                                                               the last evaluated key (or None if no more results).
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_categories = []
        try:
            # Use GSITYPE to query all validation categories
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_VALIDATION_CATEGORY"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_categories.extend(
                [itemToReceiptValidationCategory(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the validation categories.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    validation_categories.extend(
                        [
                            itemToReceiptValidationCategory(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_categories, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation categories from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Error listing receipt validation categories: {e}"
                ) from e

    def listReceiptValidationCategoriesByStatus(
        self,
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        """Returns ReceiptValidationCategories with a specific status using GSI3.

        Args:
            status (str): The status to filter by.
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation categories cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple containing a list of validation categories and
                                                               the last evaluated key (or None if no more results).
        """
        if status is None:
            raise ValueError("status parameter is required and cannot be None.")
        if not isinstance(status, str):
            raise ValueError("status must be a string.")
        if not status:
            raise ValueError("status must not be empty.")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_categories = []
        try:
            # Use GSI3 to query validation categories by status
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "begins_with(#pk, :pk_val)",
                "ExpressionAttributeNames": {"#pk": "GSI3PK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"FIELD_STATUS##{status}"},
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_categories.extend(
                [itemToReceiptValidationCategory(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the validation categories.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    validation_categories.extend(
                        [
                            itemToReceiptValidationCategory(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_categories, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation categories from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Error listing receipt validation categories: {e}"
                ) from e

    def listReceiptValidationCategoriesForReceipt(
        self,
        receipt_id: int,
        image_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        """Returns ReceiptValidationCategories for a specific receipt.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            limit (int, optional): The maximum number of results to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The last evaluated key from a previous request. Defaults to None.

        Raises:
            ValueError: If any parameters are invalid.
            Exception: If the receipt validation categories cannot be retrieved from DynamoDB.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple containing a list of validation categories and
                                                               the last evaluated key (or None if no more results).
        """
        if receipt_id is None:
            raise ValueError("receipt_id parameter is required and cannot be None.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id parameter is required and cannot be None.")
        assert_valid_uuid(image_id)
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        validation_categories = []
        try:
            # Query validation categories for a specific receipt
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pkVal AND begins_with(SK, :skPrefix)",
                "ExpressionAttributeValues": {
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#"
                    },
                },
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            validation_categories.extend(
                [itemToReceiptValidationCategory(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the validation categories.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    validation_categories.extend(
                        [
                            itemToReceiptValidationCategory(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return validation_categories, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt validation categories from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not list ReceiptValidationCategories from the database: {e}"
                ) from e

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import item_to_receipt_validation_category
from receipt_dynamo.entities.receipt_validation_category import (
    ReceiptValidationCategory,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _ReceiptValidationCategory(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to access receipt validation categories in DynamoDB.

    Methods
    -------
    add_receipt_validation_category(category: ReceiptValidationCategory)
        Adds a ReceiptValidationCategory to DynamoDB.
    add_receipt_validation_categories(categories:
            list[ReceiptValidationCategory])
        Adds multiple ReceiptValidationCategories to DynamoDB in batches.
    update_receipt_validation_category(category: ReceiptValidationCategory)
        Updates an existing ReceiptValidationCategory in the database.
    update_receipt_validation_categories(categories:
            list[ReceiptValidationCategory])
        Updates multiple ReceiptValidationCategories in the database.
    delete_receipt_validation_category(category: ReceiptValidationCategory)
        Deletes a single ReceiptValidationCategory.
    delete_receipt_validation_categories(categories:
            list[ReceiptValidationCategory])
        Deletes multiple ReceiptValidationCategories in batch.
    get_receipt_validation_category(
        receipt_id: int,
        image_id: str,
        field_name: str
    ) -> ReceiptValidationCategory:
        Retrieves a single ReceiptValidationCategory by IDs.
    list_receipt_validation_categories(
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        Returns ReceiptValidationCategories and the last evaluated key.
    list_receipt_validation_categories_by_status(
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        Returns ReceiptValidationCategories with a specific status.
    list_receipt_validation_categories_for_receipt(
        receipt_id: int,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationCategory], dict | None]:
        Returns ReceiptValidationCategories for a specific receipt.
    """

    @handle_dynamodb_errors("add_receipt_validation_category")
    def add_receipt_validation_category(
        self, category: ReceiptValidationCategory
    ):
        """Adds a ReceiptValidationCategory to DynamoDB.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory
                to add.

        Raises:
            ValueError: If the category is None or not an instance of
                ReceiptValidationCategory.
            Exception: If the category cannot be added to DynamoDB.
        """
        self._validate_entity(category, ReceiptValidationCategory, "category")
        self._add_entity(
            category,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_validation_categories")
    def add_receipt_validation_categories(
        self, categories: List[ReceiptValidationCategory]
    ):
        """Adds multiple ReceiptValidationCategories to DynamoDB in batches.

        Args:
            categories (list[ReceiptValidationCategory]): The
                ReceiptValidationCategories to add.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be added to DynamoDB.
        """
        self._validate_entity_list(
            categories, ReceiptValidationCategory, "categories"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=category.to_item())
            )
            for category in categories
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_validation_category")
    def update_receipt_validation_category(
        self, category: ReceiptValidationCategory
    ):
        """Updates an existing ReceiptValidationCategory in the database.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory
                to update.

        Raises:
            ValueError: If the category is None or not an instance of
                ReceiptValidationCategory.
            Exception: If the category cannot be updated in DynamoDB.
        """
        self._validate_entity(category, ReceiptValidationCategory, "category")
        self._update_entity(
            category,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_validation_categories")
    def update_receipt_validation_categories(
        self, categories: List[ReceiptValidationCategory]
    ):
        """Updates multiple ReceiptValidationCategories in the database.

        Args:
            categories (list[ReceiptValidationCategory]): The
                ReceiptValidationCategories to update.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be updated in DynamoDB.
        """
        self._update_entities(
            categories, ReceiptValidationCategory, "categories"
        )

    @handle_dynamodb_errors("delete_receipt_validation_category")
    def delete_receipt_validation_category(
        self, category: ReceiptValidationCategory
    ):
        """Deletes a single ReceiptValidationCategory.

        Args:
            category (ReceiptValidationCategory): The ReceiptValidationCategory
                to delete.

        Raises:
            ValueError: If the category is None or not an instance of
                ReceiptValidationCategory.
            Exception: If the category cannot be deleted from DynamoDB.
        """
        self._validate_entity(category, ReceiptValidationCategory, "category")
        self._delete_entity(category)

    @handle_dynamodb_errors("delete_receipt_validation_categories")
    def delete_receipt_validation_categories(
        self, categories: List[ReceiptValidationCategory]
    ):
        """Deletes multiple ReceiptValidationCategories in batch.

        Args:
            categories (list[ReceiptValidationCategory]): The
                ReceiptValidationCategories to delete.

        Raises:
            ValueError: If the categories are None or not a list.
            Exception: If the categories cannot be deleted from DynamoDB.
        """
        self._validate_entity_list(
            categories, ReceiptValidationCategory, "categories"
        )

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=category.key)
            )
            for category in categories
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_validation_category")
    def get_receipt_validation_category(
        self, receipt_id: int, image_id: str, field_name: str
    ) -> ReceiptValidationCategory:
        """Retrieves a single ReceiptValidationCategory by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            field_name (str): The field name of the category.

        Returns:
            ReceiptValidationCategory: The retrieved ReceiptValidationCategory.

        Raises:
            ValueError: If the IDs are invalid.
            Exception: If the ReceiptValidationCategory cannot be retrieved
                from DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(field_name, str):
            raise EntityValidationError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY"
                f"#{field_name}"
            ),
            entity_class=ReceiptValidationCategory,
            converter_func=item_to_receipt_validation_category,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptValidationCategory for receipt {receipt_id}, "
                f"image {image_id}, and field {field_name} does not exist"
            )

        return result

    @handle_dynamodb_errors("list_receipt_validation_categories")
    def list_receipt_validation_categories(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationCategory], Optional[Dict]]:
        """Returns ReceiptValidationCategories and the last evaluated key.

        Args:
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple
                containing the list of ReceiptValidationCategories and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the limit or last_evaluated_key are invalid.
            Exception: If the ReceiptValidationCategories cannot be retrieved
                from DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":val": {"S": "RECEIPT_VALIDATION_CATEGORY"}
            },
            converter_func=item_to_receipt_validation_category,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_validation_categories_by_status")
    def list_receipt_validation_categories_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationCategory], Optional[Dict]]:
        """Returns ReceiptValidationCategories with a specific status.

        Args:
            status (str): The status to filter by.
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple
                containing the list of ReceiptValidationCategories and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the parameters are invalid.
            Exception: If the ReceiptValidationCategories cannot be retrieved
                from DynamoDB.
        """
        if not isinstance(status, str):
            raise EntityValidationError(
                f"status must be a string, got {type(status).__name__}"
            )
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#gsi1pk = :pk",
            expression_attribute_names={"#gsi1pk": "GSI1PK"},
            expression_attribute_values={
                ":pk": {"S": f"VALIDATION_STATUS#{status}"}
            },
            converter_func=item_to_receipt_validation_category,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_validation_categories_for_receipt")
    def list_receipt_validation_categories_for_receipt(
        self,
        receipt_id: int,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationCategory], Optional[Dict]]:
        """Returns ReceiptValidationCategories for a specific receipt.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationCategory], dict | None]: A tuple
                containing the list of ReceiptValidationCategories and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the parameters are invalid.
            Exception: If the ReceiptValidationCategories cannot be retrieved
                from DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        return self._query_entities(
            index_name=None,
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#"
                        "CATEGORY#"
                    )
                },
            },
            converter_func=item_to_receipt_validation_category,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

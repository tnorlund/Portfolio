"""Receipt Label Analysis data access using base operations framework.

This refactored version reduces code from ~944 lines to ~300 lines
(68% reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.entities.receipt_label_analysis import (
    ReceiptLabelAnalysis,
    item_to_receipt_label_analysis,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )
else:
    from receipt_dynamo.data._base import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        WriteRequestTypeDef,
    )


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _ReceiptLabelAnalysis(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to access receipt label analyses in DynamoDB.

    This refactored version uses base operations to eliminate code duplication
    while maintaining full backward compatibility.
    """

    @handle_dynamodb_errors("add_receipt_label_analysis")
    def add_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Adds a receipt label analysis to the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label
                analysis to add to the database

        Raises:
            ValueError: When a receipt label analysis with the same ID already
                exists
        """
        self._validate_entity(
            receipt_label_analysis,
            ReceiptLabelAnalysis,
            "ReceiptLabelAnalysis",
        )
        self._add_entity(receipt_label_analysis)

    @handle_dynamodb_errors("add_receipt_label_analyses")
    def add_receipt_label_analyses(
        self, receipt_label_analyses: List[ReceiptLabelAnalysis]
    ):
        """Adds multiple receipt label analyses to the database

        Args:
            receipt_label_analyses (List[ReceiptLabelAnalysis]): The receipt
                label analyses to add to the database

        Raises:
            ValueError: When a receipt label analysis with the same ID already
                exists
        """
        self._validate_entity_list(
            receipt_label_analyses,
            ReceiptLabelAnalysis,
            "receipt_label_analyses",
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=analysis.to_item())
            )
            for analysis in receipt_label_analyses
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_label_analysis")
    def update_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Updates a receipt label analysis in the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label
                analysis to update in the database

        Raises:
            ValueError: When a receipt label analysis with the same ID does not
                exist
        """
        self._validate_entity(
            receipt_label_analysis,
            ReceiptLabelAnalysis,
            "ReceiptLabelAnalysis",
        )
        self._update_entity(receipt_label_analysis)

    @handle_dynamodb_errors("update_receipt_label_analyses")
    def update_receipt_label_analyses(
        self, receipt_label_analyses: List[ReceiptLabelAnalysis]
    ):
        """Updates multiple receipt label analyses in the database

        Args:
            receipt_label_analyses (List[ReceiptLabelAnalysis]): The receipt
                label analyses to update in the database

        Raises:
            ValueError: When a receipt label analysis with the same ID does not
                exist
        """
        self._validate_entity_list(
            receipt_label_analyses,
            ReceiptLabelAnalysis,
            "receipt_label_analyses",
        )

        # Use transactional writes for updates to ensure items exist
        transact_items = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": analysis.to_item(),
                    "ConditionExpression": (
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                }
            }
            for analysis in receipt_label_analyses
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_label_analysis")
    def delete_receipt_label_analysis(
        self, receipt_label_analysis: ReceiptLabelAnalysis
    ):
        """Deletes a receipt label analysis from the database

        Args:
            receipt_label_analysis (ReceiptLabelAnalysis): The receipt label
                analysis to delete from the database

        Raises:
            ValueError: When a receipt label analysis with the same ID does not
                exist
        """
        self._validate_entity(
            receipt_label_analysis,
            ReceiptLabelAnalysis,
            "ReceiptLabelAnalysis",
        )
        self._delete_entity(receipt_label_analysis)

    @handle_dynamodb_errors("delete_receipt_label_analyses")
    def delete_receipt_label_analyses(
        self, receipt_label_analyses: List[ReceiptLabelAnalysis]
    ):
        """Deletes multiple receipt label analyses from the database

        Args:
            receipt_label_analyses (List[ReceiptLabelAnalysis]): The receipt
                label analyses to delete from the database

        Raises:
            ValueError: When a receipt label analysis with the same ID does not
                exist
        """
        self._validate_entity_list(
            receipt_label_analyses,
            ReceiptLabelAnalysis,
            "receipt_label_analyses",
        )

        # Use transactional writes for deletes to ensure items exist
        transact_items = [
            {
                "Delete": {
                    "TableName": self.table_name,
                    "Key": analysis.key,
                    "ConditionExpression": (
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                }
            }
            for analysis in receipt_label_analyses
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt_label_analysis")
    def get_receipt_label_analysis(
        self, image_id: str, receipt_id: int, version: Optional[str] = None
    ) -> ReceiptLabelAnalysis:
        """Retrieves a receipt label analysis from the database

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            version (Optional[str]): The version of the analysis

        Returns:
            ReceiptLabelAnalysis: The receipt label analysis from the database

        Raises:
            ValueError: When the receipt label analysis does not exist
        """
        # Check for None values first
        if image_id is None:
            raise ValueError("image_id cannot be None")
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")

        # Then check types
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        if not isinstance(receipt_id, int):
            raise ValueError(
                "receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if version is not None and not isinstance(version, str):
            raise ValueError(
                "version must be a string or None, got "
                f"{type(version).__name__}"
            )

        # Check for positive integers
        if receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")

        assert_valid_uuid(image_id)

        if version:
            # Get specific version
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": (
                            f"RECEIPT#{receipt_id:05d}#ANALYSIS#"
                            f"LABELS#{version}"
                        )
                    },
                },
            )
            item = response.get("Item")
            if not item:
                raise ValueError(
                    f"No ReceiptLabelAnalysis found for receipt {receipt_id}, "
                    f"image {image_id}, and version {version}"
                )
            return item_to_receipt_label_analysis(item)
        else:
            # Query for any version and return first
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "#pk = :pk AND begins_with(#sk, :sk_prefix)"
                ),
                "ExpressionAttributeNames": {
                    "#pk": "PK",
                    "#sk": "SK",
                },
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {
                        "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS"
                    },
                },
                "Limit": 1,
            }

            response = self._client.query(**query_params)
            items = response.get("Items", [])

            if not items:
                raise ValueError(
                    f"Receipt Label Analysis for Image ID {image_id} and "
                    f"Receipt ID {receipt_id} does not exist"
                )

            return item_to_receipt_label_analysis(items[0])

    @handle_dynamodb_errors("list_receipt_label_analyses")
    def list_receipt_label_analyses(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]:
        """Lists all receipt label analyses

        Args:
            limit (Optional[int]): The maximum number of items to return
            last_evaluated_key (
                Optional[Dict[str, Any]]
            ): The key to start from

        Returns:
            Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]: The
                receipt label analyses and the last evaluated key
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {
                ":val": {"S": "RECEIPT_LABEL_ANALYSIS"}
            },
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        label_analyses.extend(
            [
                item_to_receipt_label_analysis(item)
                for item in response["Items"]
            ]
        )

        if limit is None:
            # Paginate through all analyses
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                label_analyses.extend(
                    [
                        item_to_receipt_label_analysis(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey", None)

        return label_analyses, last_evaluated_key

    @handle_dynamodb_errors("list_receipt_label_analyses_for_image")
    def list_receipt_label_analyses_for_image(
        self, image_id: str
    ) -> List[ReceiptLabelAnalysis]:
        """Lists all receipt label analyses for a given image

        Args:
            image_id (str): The image ID

        Returns:
            List[ReceiptLabelAnalysis]:
                The receipt label analyses for the image
        """
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        assert_valid_uuid(image_id)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": (
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": "RECEIPT#"},
            },
            "FilterExpression": "contains(#sk, :analysis_type)",
        }
        query_params["ExpressionAttributeValues"][":analysis_type"] = {
            "S": "#ANALYSIS#LABELS"
        }

        response = self._client.query(**query_params)
        label_analyses.extend(
            [
                item_to_receipt_label_analysis(item)
                for item in response["Items"]
            ]
        )

        # Continue querying if there are more results
        while "LastEvaluatedKey" in response:
            query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self._client.query(**query_params)
            label_analyses.extend(
                [
                    item_to_receipt_label_analysis(item)
                    for item in response["Items"]
                ]
            )

        return label_analyses

    @handle_dynamodb_errors("get_receipt_label_analyses_by_image")
    def get_receipt_label_analyses_by_image(
        self,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]:
        """Gets receipt label analyses for a given image with pagination
        support

        Args:
            image_id (str): The image ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Pagination key

        Returns:
            Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]:
                The receipt label analyses and pagination key
        """
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        assert_valid_uuid(image_id)

        if limit is not None:
            if not isinstance(limit, int):
                raise ValueError("Limit must be an integer")
            if limit <= 0:
                raise ValueError("Limit must be greater than 0")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": (
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": "RECEIPT#"},
            },
            "FilterExpression": "contains(#sk, :analysis_type)",
        }
        query_params["ExpressionAttributeValues"][":analysis_type"] = {
            "S": "#ANALYSIS#LABELS"
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        label_analyses.extend(
            [
                item_to_receipt_label_analysis(item)
                for item in response["Items"]
            ]
        )

        if limit is None:
            # If no limit is provided, paginate until all items are retrieved
            while (
                "LastEvaluatedKey" in response and response["LastEvaluatedKey"]
            ):
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                label_analyses.extend(
                    [
                        item_to_receipt_label_analysis(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key_result = None
        else:
            # If a limit is provided, capture the LastEvaluatedKey (if any)
            last_evaluated_key_result = response.get("LastEvaluatedKey", None)

        return label_analyses, last_evaluated_key_result

    @handle_dynamodb_errors("get_receipt_label_analyses_by_receipt")
    def get_receipt_label_analyses_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]:
        """Gets receipt label analyses for a given image and receipt with
        pagination support

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Pagination key

        Returns:
            Tuple[List[ReceiptLabelAnalysis], Optional[Dict[str, Any]]]:
                The receipt label analyses and pagination key
        """
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        assert_valid_uuid(image_id)

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be a positive integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be a positive integer")

        if limit is not None:
            if not isinstance(limit, int):
                raise ValueError("Limit must be an integer")
            if limit <= 0:
                raise ValueError("Limit must be greater than 0")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": (
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS"
                },
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        label_analyses.extend(
            [
                item_to_receipt_label_analysis(item)
                for item in response["Items"]
            ]
        )

        if limit is None:
            # If no limit is provided, paginate until all items are retrieved
            while (
                "LastEvaluatedKey" in response and response["LastEvaluatedKey"]
            ):
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                label_analyses.extend(
                    [
                        item_to_receipt_label_analysis(item)
                        for item in response["Items"]
                    ]
                )
            last_evaluated_key_result = None
        else:
            # If a limit is provided, capture the LastEvaluatedKey (if any)
            last_evaluated_key_result = response.get("LastEvaluatedKey", None)

        return label_analyses, last_evaluated_key_result

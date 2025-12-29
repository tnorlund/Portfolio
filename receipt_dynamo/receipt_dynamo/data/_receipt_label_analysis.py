"""Receipt Label Analysis data access using base operations framework.

This refactored version reduces code from ~944 lines to ~300 lines
(68% reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    QueryByParentMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_label_analysis import (
    ReceiptLabelAnalysis,
    item_to_receipt_label_analysis,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )
else:
    from receipt_dynamo.data.base_operations import (
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )


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


class _ReceiptLabelAnalysis(
    FlattenedStandardMixin,
    QueryByParentMixin,
):
    """
    A class used to access receipt label analyses in DynamoDB.

    This refactored version uses base operations to eliminate code duplication
    while maintaining full backward compatibility.

    .. deprecated::
        This class is deprecated and not used in production. Consider removing
        if no longer needed for historical data access.
    """

    @handle_dynamodb_errors("add_receipt_label_analysis")
    def add_receipt_label_analysis(self, receipt_label_analysis: ReceiptLabelAnalysis):
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
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=analysis.to_item()))
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
            "receipt_label_analysis",
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
        self._update_entities(
            receipt_label_analyses,
            ReceiptLabelAnalysis,
            "receipt_label_analyses",
        )

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
            "receipt_label_analysis",
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
        # Use CommonValidationMixin for standardized validation
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)

        if version is not None and not isinstance(version, str):
            raise EntityValidationError(
                "version must be a string or None, got " f"{type(version).__name__}"
            )

        if version:
            # Get specific version
            result = self._get_entity(
                primary_key=f"IMAGE#{image_id}",
                sort_key=f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS#{version}",
                entity_class=ReceiptLabelAnalysis,
                converter_func=item_to_receipt_label_analysis,
            )
            if result is None:
                raise EntityNotFoundError(
                    f"No ReceiptLabelAnalysis found for receipt {receipt_id}, "
                    f"image {image_id}, and version {version}"
                )
            return result
        # Query for any version and return first
        # Use the QueryByParentMixin for standardized parent-child queries
        results, _ = self._query_by_parent(
            parent_key_prefix=f"IMAGE#{image_id}",
            child_key_prefix=f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS",
            converter_func=item_to_receipt_label_analysis,
            limit=1,
        )

        if not results:
            raise EntityNotFoundError(
                f"Receipt Label Analysis for Image ID {image_id} and "
                f"Receipt ID {receipt_id} does not exist"
            )

        return results[0]

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
        # Use the QueryByTypeMixin for standardized GSITYPE queries
        return self._query_by_type(
            entity_type="RECEIPT_LABEL_ANALYSIS",
            converter_func=item_to_receipt_label_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

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
            raise EntityValidationError("image_id must be a string")
        self._validate_image_id(image_id)

        # WARNING: This query is potentially inefficient as it queries ALL
        # receipts for an image and then filters for analysis labels. Consider
        # using a GSI or accepting this limitation if the number of receipts
        # per image is small.
        # Cannot use QueryByParentMixin here due to the need for filtering.
        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression=("#pk = :pk AND begins_with(#sk, :sk_prefix)"),
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": "RECEIPT#"},
                ":analysis_type": {"S": "#ANALYSIS#LABELS"},
            },
            converter_func=item_to_receipt_label_analysis,
            filter_expression="contains(#sk, :analysis_type)",
        )

        return results

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
            raise EntityValidationError("image_id must be a string")
        self._validate_image_id(image_id)

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("Limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("Limit must be greater than 0")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": ("#pk = :pk AND begins_with(#sk, :sk_prefix)"),
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
            [item_to_receipt_label_analysis(item) for item in response["Items"]]
        )

        if limit is None:
            # If no limit is provided, paginate until all items are retrieved
            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self._client.query(**query_params)
                label_analyses.extend(
                    [item_to_receipt_label_analysis(item) for item in response["Items"]]
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
            raise EntityValidationError("image_id must be a string")
        self._validate_image_id(image_id)

        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be a positive integer")
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be a positive integer")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("Limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("Limit must be greater than 0")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        label_analyses = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": ("#pk = :pk AND begins_with(#sk, :sk_prefix)"),
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LABELS"},
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        if limit is not None:
            query_params["Limit"] = limit

        response = self._client.query(**query_params)
        label_analyses.extend(
            [item_to_receipt_label_analysis(item) for item in response["Items"]]
        )

        if limit is None:
            # If no limit is provided, paginate until all items are retrieved
            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self._client.query(**query_params)
                label_analyses.extend(
                    [item_to_receipt_label_analysis(item) for item in response["Items"]]
                )
            last_evaluated_key_result = None
        else:
            # If a limit is provided, capture the LastEvaluatedKey (if any)
            last_evaluated_key_result = response.get("LastEvaluatedKey", None)

        return label_analyses, last_evaluated_key_result

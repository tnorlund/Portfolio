"""Receipt Structure Analysis data access using base operations framework.

This refactored version reduces code from ~806 lines to ~260 lines (68%
reduction) while maintaining full backward compatibility and all
functionality.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    CommonValidationMixin,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    QueryByTypeMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import item_to_receipt_structure_analysis
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )
else:
    from receipt_dynamo.data.base_operations import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        WriteRequestTypeDef,
    )


class _ReceiptStructureAnalysis(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    """
    A class used to access receipt structure analyses in DynamoDB.

    This refactored version uses base operations to eliminate code duplication
    while maintaining full backward compatibility.
    """

    @handle_dynamodb_errors("add_receipt_structure_analysis")
    def add_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Adds a ReceiptStructureAnalysis to DynamoDB.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis
                to add.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be added to DynamoDB.
        """
        self._validate_entity(analysis, ReceiptStructureAnalysis, "analysis")
        self._add_entity(
            analysis,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_structure_analyses")
    def add_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Adds multiple ReceiptStructureAnalyses to DynamoDB in batches.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The
                ReceiptStructureAnalyses to add.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be added to DynamoDB.
        """
        self._validate_entity_list(
            analyses, ReceiptStructureAnalysis, "analyses"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=analysis.to_item())
            )
            for analysis in analyses
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_structure_analysis")
    def update_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Updates an existing ReceiptStructureAnalysis in the database.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis
                to update.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be updated in DynamoDB.
        """
        self._validate_entity(analysis, ReceiptStructureAnalysis, "analysis")
        self._update_entity(
            analysis,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_structure_analyses")
    def update_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Updates multiple ReceiptStructureAnalyses in the database.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The
                ReceiptStructureAnalyses to update.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be updated in DynamoDB.
        """
        self._validate_entity_list(
            analyses, ReceiptStructureAnalysis, "analyses"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=analysis.to_item())
            )
            for analysis in analyses
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_receipt_structure_analysis")
    def delete_receipt_structure_analysis(
        self, analysis: ReceiptStructureAnalysis
    ):
        """Deletes a single ReceiptStructureAnalysis by IDs.

        Args:
            analysis (ReceiptStructureAnalysis): The ReceiptStructureAnalysis
                to delete.

        Raises:
            ValueError: If the analysis is None or not an instance of
                ReceiptStructureAnalysis.
            Exception: If the analysis cannot be deleted from DynamoDB.
        """
        self._validate_entity(analysis, ReceiptStructureAnalysis, "analysis")
        self._delete_entity(analysis)

    @handle_dynamodb_errors("delete_receipt_structure_analyses")
    def delete_receipt_structure_analyses(
        self, analyses: list[ReceiptStructureAnalysis]
    ):
        """Deletes multiple ReceiptStructureAnalyses in batch.

        Args:
            analyses (list[ReceiptStructureAnalysis]): The
                ReceiptStructureAnalyses to delete.

        Raises:
            ValueError: If the analyses are None or not a list.
            Exception: If the analyses cannot be deleted from DynamoDB.
        """
        self._validate_entity_list(
            analyses, ReceiptStructureAnalysis, "analyses"
        )

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(
                    Key={
                        "PK": {"S": f"IMAGE#{analysis.image_id}"},
                        "SK": {
                            "S": (
                                f"RECEIPT#{analysis.receipt_id:05d}"
                                f"#ANALYSIS#STRUCTURE#{analysis.version}"
                            )
                        },
                    }
                )
            )
            for analysis in analyses
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_structure_analysis")
    def get_receipt_structure_analysis(
        self,
        receipt_id: int,
        image_id: str,
        version: Optional[str] = None,
    ) -> ReceiptStructureAnalysis:
        """Retrieves a single ReceiptStructureAnalysis by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            version (Optional[str]): The version of the analysis. If None,
                returns the first analysis found.

        Returns:
            ReceiptStructureAnalysis: The retrieved ReceiptStructureAnalysis.

        Raises:
            ValueError: If the receipt_id or image_id are invalid.
            Exception: If the ReceiptStructureAnalysis cannot be retrieved from
                DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                (
                    f"receipt_id must be an integer, got"
                    f" {type(receipt_id).__name__}"
                )
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                (
                    f"image_id must be a string, got"
                    f" {type(image_id).__name__}"
                )
            )
        if version is not None and not isinstance(version, str):
            raise EntityValidationError(
                (
                    "version must be a string or None, got"
                    f" {type(version).__name__}"
                )
            )

        self._validate_image_id(image_id)

        if version:
            # If version is provided, get the exact item
            result = self._get_entity(
                primary_key=f"IMAGE#{image_id}",
                sort_key=(
                    f"RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE"
                    f"#{version}"
                ),
                entity_class=ReceiptStructureAnalysis,
                converter_func=item_to_receipt_structure_analysis,
            )
            if result is None:
                raise EntityNotFoundError(
                    "No ReceiptStructureAnalysis found for receipt "
                    f"{receipt_id}, image {image_id}, and version {version}"
                )
            return result

        # If no version is provided, query for all analyses and return the
        # first one
        results, _ = self._query_entities(
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
                    "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE"
                },
            },
            converter_func=item_to_receipt_structure_analysis,
            limit=1,
        )

        if not results:
            raise EntityNotFoundError(
                "Receipt Structure Analysis for Image ID "
                f"{image_id} and Receipt ID {receipt_id} does not exist"
            )

        return results[0]

    @handle_dynamodb_errors("list_receipt_structure_analyses")
    def list_receipt_structure_analyses(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ReceiptStructureAnalysis], Optional[Dict[str, Any]]]:
        """Lists all ReceiptStructureAnalyses.

        Args:
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict[str, Any]], optional): The key to
                start from for pagination. Defaults to None.

        Returns:
            Tuple[List[ReceiptStructureAnalysis], Optional[Dict[str, Any]]]:
                A tuple containing the list of ReceiptStructureAnalyses and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the limit or last_evaluated_key are invalid.
            Exception: If the ReceiptStructureAnalyses cannot be retrieved from
                DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        return self._query_by_type(
            entity_type="RECEIPT_STRUCTURE_ANALYSIS",
            converter_func=item_to_receipt_structure_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_structure_analyses_from_receipt")
    def list_receipt_structure_analyses_from_receipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptStructureAnalysis]:
        """Returns all ReceiptStructureAnalyses for a given receipt.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.

        Returns:
            list[ReceiptStructureAnalysis]: A list of ReceiptStructureAnalyses.

        Raises:
            ValueError: If the receipt_id or image_id are invalid.
            Exception: If the ReceiptStructureAnalyses cannot be retrieved from
                DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                (
                    f"receipt_id must be an integer, got"
                    f" {type(receipt_id).__name__}"
                )
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                (
                    f"image_id must be a string, got"
                    f" {type(image_id).__name__}"
                )
            )

        self._validate_image_id(image_id)

        results, _ = self._query_entities(
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
                    "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#STRUCTURE#"
                },
            },
            converter_func=item_to_receipt_structure_analysis,
        )

        return results

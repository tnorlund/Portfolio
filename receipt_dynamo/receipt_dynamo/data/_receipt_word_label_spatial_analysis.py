"""Receipt Word Label Spatial Analysis data access using base operations framework."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (
    ReceiptWordLabelSpatialAnalysis,
    item_to_receipt_word_label_spatial_analysis,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        PutRequestTypeDef,
        WriteRequestTypeDef,
    )


class _ReceiptWordLabelSpatialAnalysis(
    FlattenedStandardMixin,
):
    """
    A class used to access receipt word label spatial analysis entities in DynamoDB.

    This class provides methods to store and retrieve spatial relationship data
    between valid receipt word labels, supporting label validation workflows
    through efficient GSI-based access patterns.
    """

    @handle_dynamodb_errors("add_receipt_word_label_spatial_analysis")
    def add_receipt_word_label_spatial_analysis(
        self, spatial_analysis: ReceiptWordLabelSpatialAnalysis
    ) -> None:
        """Adds a receipt word label spatial analysis to the database

        Args:
            spatial_analysis (ReceiptWordLabelSpatialAnalysis): The spatial
                analysis to add to the database

        Raises:
            EntityValidationError: When validation fails
            EntityAlreadyExistsError: When an analysis with the same ID
                already exists
        """
        self._validate_entity(
            spatial_analysis,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analysis",
        )
        self._add_entity(spatial_analysis)

    @handle_dynamodb_errors("add_receipt_word_label_spatial_analyses")
    def add_receipt_word_label_spatial_analyses(
        self, spatial_analyses: List[ReceiptWordLabelSpatialAnalysis]
    ) -> None:
        """Adds multiple spatial analyses to the database

        Args:
            spatial_analyses (List[ReceiptWordLabelSpatialAnalysis]): The
                spatial analyses to add to the database

        Raises:
            EntityValidationError: When validation fails
        """
        self._validate_entity_list(
            spatial_analyses,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analyses",
        )

        from receipt_dynamo.data.base_operations import (
            PutRequestTypeDef,
            WriteRequestTypeDef,
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=analysis.to_item())
            )
            for analysis in spatial_analyses
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_word_label_spatial_analysis")
    def update_receipt_word_label_spatial_analysis(
        self, spatial_analysis: ReceiptWordLabelSpatialAnalysis
    ) -> None:
        """Updates a spatial analysis in the database

        Args:
            spatial_analysis (ReceiptWordLabelSpatialAnalysis): The spatial
                analysis to update

        Raises:
            EntityValidationError: When validation fails
            EntityNotFoundError: When the spatial analysis does not exist
        """
        self._validate_entity(
            spatial_analysis,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analysis",
        )
        self._update_entity(spatial_analysis)

    @handle_dynamodb_errors("update_receipt_word_label_spatial_analyses")
    def update_receipt_word_label_spatial_analyses(
        self, spatial_analyses: List[ReceiptWordLabelSpatialAnalysis]
    ) -> None:
        """Updates multiple spatial analyses in the database

        Args:
            spatial_analyses (List[ReceiptWordLabelSpatialAnalysis]): The
                spatial analyses to update

        Raises:
            EntityValidationError: When validation fails
        """
        self._update_entities(
            spatial_analyses,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analyses",
        )

    @handle_dynamodb_errors("delete_receipt_word_label_spatial_analysis")
    def delete_receipt_word_label_spatial_analysis(
        self, spatial_analysis: ReceiptWordLabelSpatialAnalysis
    ) -> None:
        """Deletes a spatial analysis from the database

        Args:
            spatial_analysis (ReceiptWordLabelSpatialAnalysis): The spatial
                analysis to delete

        Raises:
            EntityValidationError: When validation fails
        """
        self._validate_entity(
            spatial_analysis,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analysis",
        )
        self._delete_entity(spatial_analysis)

    @handle_dynamodb_errors("delete_receipt_word_label_spatial_analyses")
    def delete_receipt_word_label_spatial_analyses(
        self, spatial_analyses: List[ReceiptWordLabelSpatialAnalysis]
    ) -> None:
        """Deletes multiple spatial analyses from the database

        Args:
            spatial_analyses (List[ReceiptWordLabelSpatialAnalysis]): The
                spatial analyses to delete

        Raises:
            EntityValidationError: When validation fails
        """
        self._validate_entity_list(
            spatial_analyses,
            ReceiptWordLabelSpatialAnalysis,
            "spatial_analyses",
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
            for analysis in spatial_analyses
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt_word_label_spatial_analysis")
    def get_receipt_word_label_spatial_analysis(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
    ) -> ReceiptWordLabelSpatialAnalysis:
        """Retrieves a spatial analysis from the database

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            line_id (int): The line ID
            word_id (int): The word ID

        Returns:
            ReceiptWordLabelSpatialAnalysis: The spatial analysis from the
                database

        Raises:
            EntityValidationError: When validation fails
            EntityNotFoundError: When the spatial analysis does not exist
        """
        # Validate inputs
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if line_id is None:
            raise EntityValidationError("line_id cannot be None")
        if word_id is None:
            raise EntityValidationError("word_id cannot be None")

        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(line_id, int):
            raise EntityValidationError(
                f"line_id must be an integer, got {type(line_id).__name__}"
            )
        if not isinstance(word_id, int):
            raise EntityValidationError(
                f"word_id must be an integer, got {type(word_id).__name__}"
            )

        # Validate positive integers
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be positive")
        if line_id <= 0:
            raise EntityValidationError("line_id must be positive")
        if word_id <= 0:
            raise EntityValidationError("word_id must be positive")

        assert_valid_uuid(image_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#"
                f"WORD#{word_id:05d}#SPATIAL_ANALYSIS"
            ),
            entity_class=ReceiptWordLabelSpatialAnalysis,
            converter_func=item_to_receipt_word_label_spatial_analysis,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Receipt word label spatial analysis for "
                f"image_id={image_id}, receipt_id={receipt_id}, "
                f"line_id={line_id}, word_id={word_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("list_spatial_analyses_for_receipt")
    def list_spatial_analyses_for_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]
    ]:
        """Lists all spatial analyses for a specific receipt using GSI2

        Args:
            image_id (str): The image ID
            receipt_id (int): The receipt ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]]:
                The spatial analyses for the receipt and last evaluated key

        Raises:
            EntityValidationError: When validation fails
        """
        # Validate inputs
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be positive")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        assert_valid_uuid(image_id)

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "GSI2PK"},
            expression_attribute_values={
                ":pk": {
                    "S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#SPATIAL"
                }
            },
            converter_func=item_to_receipt_word_label_spatial_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_spatial_analyses_by_label")
    def get_spatial_analyses_by_label(
        self,
        label: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]
    ]:
        """Retrieves spatial analyses by label type using GSI1

        This method enables cross-receipt analysis of spatial patterns for
        a specific label type, supporting label validation workflows.

        Args:
            label (str): The label type to search for
            limit (Optional[int]): Maximum number of analyses to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]]:
                The spatial analyses and last evaluated key

        Raises:
            EntityValidationError: When validation fails
        """
        if not isinstance(label, str) or not label:
            raise EntityValidationError("label must be a non-empty string")

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        # Use uppercase label for GSI1PK
        label_upper = label.upper()

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "GSI1PK"},
            expression_attribute_values={
                ":pk": {"S": f"SPATIAL_ANALYSIS#{label_upper}"}
            },
            converter_func=item_to_receipt_word_label_spatial_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_spatial_analyses_for_image")
    def list_spatial_analyses_for_image(
        self,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]
    ]:
        """Lists all spatial analyses for a given image

        Args:
            image_id (str): The image ID
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]]:
                The spatial analyses for the image and last evaluated key

        Raises:
            EntityValidationError: When validation fails
        """
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )

        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        assert_valid_uuid(image_id)

        # Since we need to find SK patterns ending with SPATIAL_ANALYSIS,
        # we'll scan the partition but this should be limited to one image
        return self._query_entities(
            index_name=None,  # Use main table
            key_condition_expression="#pk = :pk",
            expression_attribute_names={
                "#pk": "PK",
                "#type": "TYPE",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":type": {"S": "RECEIPT_WORD_LABEL_SPATIAL_ANALYSIS"},
            },
            filter_expression="#type = :type",
            converter_func=item_to_receipt_word_label_spatial_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_word_label_spatial_analyses")
    def list_receipt_word_label_spatial_analyses(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]
    ]:
        """Lists all spatial analyses with pagination

        Args:
            limit (Optional[int]): Maximum number of items to return
            last_evaluated_key (Optional[Dict[str, Any]]): Key to start from

        Returns:
            Tuple[List[ReceiptWordLabelSpatialAnalysis], Optional[Dict[str, Any]]]:
                The spatial analyses and last evaluated key

        Raises:
            EntityValidationError: When validation fails
        """
        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("limit must be greater than 0")

        return self._query_by_type(
            entity_type="RECEIPT_WORD_LABEL_SPATIAL_ANALYSIS",
            converter_func=item_to_receipt_word_label_spatial_analysis,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

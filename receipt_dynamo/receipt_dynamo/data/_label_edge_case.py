"""Label Edge Case data access using base operations framework."""

from typing import Any, Dict, List, Optional

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.label_edge_case import (
    LabelEdgeCase,
    item_to_label_edge_case,
)


class _LabelEdgeCase(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    """
    A class used to access label edge cases in DynamoDB.

    Edge cases are stored with:
    - PK: CONFIG#EDGE_CASES
    - SK: LABEL#{label_type}#MERCHANT#{merchant_name}#WORD#{normalized_word}
      or SK: LABEL#{label_type}#WORD#{normalized_word} for global cases
    """

    @handle_dynamodb_errors("get_edge_cases_for_label")
    def get_edge_cases_for_label(
        self,
        label_type: str,
        merchant_name: Optional[str] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[LabelEdgeCase], Optional[Dict[str, Any]]]:
        """
        Get edge cases for a specific label type.

        Args:
            label_type: The CORE_LABEL type (e.g., "PHONE_NUMBER")
            merchant_name: Optional merchant name to filter by
            limit: Maximum number of results
            last_evaluated_key: For pagination

        Returns:
            Tuple of (list of edge cases, last evaluated key)
        """
        if not isinstance(label_type, str) or not label_type:
            raise EntityValidationError("label_type must be a non-empty string")

        label_type = label_type.upper()

        # Build SK prefix
        if merchant_name:
            sk_prefix = f"LABEL#{label_type}#MERCHANT#{merchant_name}#WORD#"
        else:
            sk_prefix = f"LABEL#{label_type}#WORD#"

        # Query by PK and SK prefix
        return self._query_entities(
            key_condition_expression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": "CONFIG#EDGE_CASES"},
                ":sk_prefix": {"S": sk_prefix},
            },
            converter_func=item_to_label_edge_case,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("add_edge_case")
    def add_edge_case(self, edge_case: LabelEdgeCase) -> None:
        """
        Add an edge case to DynamoDB.

        Args:
            edge_case: The LabelEdgeCase to add

        Raises:
            EntityAlreadyExistsError: If edge case already exists
        """
        self._validate_entity(edge_case, LabelEdgeCase, "edge_case")
        self._add_entity(edge_case)

    @handle_dynamodb_errors("update_edge_case_statistics")
    def update_edge_case_statistics(
        self,
        edge_case: LabelEdgeCase,
        times_identified: Optional[int] = None,
        false_positives: Optional[int] = None,
    ) -> None:
        """
        Update statistics for an edge case.

        Args:
            edge_case: The edge case to update
            times_identified: New value for times_identified (optional)
            false_positives: New value for false_positives (optional)
        """
        self._validate_entity(edge_case, LabelEdgeCase, "edge_case")

        # Update statistics
        if times_identified is not None:
            edge_case.times_identified = times_identified
        if false_positives is not None:
            edge_case.false_positives = false_positives

        # Update timestamp
        from datetime import datetime

        edge_case.updated_at = datetime.now().isoformat()

        # Update in DynamoDB
        self._update_entity(edge_case)

    def check_edge_case_match(
        self,
        word_text: str,
        label_type: str,
        merchant_name: Optional[str] = None,
    ) -> Optional[LabelEdgeCase]:
        """
        Check if a word/label combination matches any edge case.

        Checks merchant-specific edge cases first, then global edge cases.
        Returns the first matching edge case found.

        Args:
            word_text: The word text to check
            label_type: The CORE_LABEL type
            merchant_name: Optional merchant name for merchant-specific checks

        Returns:
            Matching LabelEdgeCase if found, None otherwise
        """
        if not isinstance(word_text, str):
            raise EntityValidationError("word_text must be a string")
        if not isinstance(label_type, str) or not label_type:
            raise EntityValidationError("label_type must be a non-empty string")

        label_type = label_type.upper()
        normalized_word = word_text.strip().upper()

        # First check merchant-specific edge cases if merchant_name provided
        if merchant_name:
            merchant_cases, _ = self.get_edge_cases_for_label(
                label_type=label_type,
                merchant_name=merchant_name,
                limit=100,  # Reasonable limit for edge cases per merchant
            )

            for case in merchant_cases:
                if case.matches(word_text, merchant_name):
                    return case

        # Then check global edge cases
        global_cases, _ = self.get_edge_cases_for_label(
            label_type=label_type,
            merchant_name=None,
            limit=100,  # Reasonable limit for global edge cases
        )

        for case in global_cases:
            if case.matches(word_text):
                return case

        return None

    @handle_dynamodb_errors("get_all_edge_cases")
    def get_all_edge_cases(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[LabelEdgeCase], Optional[Dict[str, Any]]]:
        """
        Get all edge cases (across all label types).

        Args:
            limit: Maximum number of results
            last_evaluated_key: For pagination

        Returns:
            Tuple of (list of edge cases, last evaluated key)
        """
        return self._query_entities(
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "PK"},
            expression_attribute_values={":pk": {"S": "CONFIG#EDGE_CASES"}},
            converter_func=item_to_label_edge_case,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )


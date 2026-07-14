from dataclasses import dataclass

from receipt_dynamo.entities.receipt_chatgpt_validation import (
    ReceiptChatGPTValidation,
)
from receipt_dynamo.entities.receipt_label_analysis import ReceiptLabelAnalysis
from receipt_dynamo.entities.receipt_line_item_analysis import (
    ReceiptLineItemAnalysis,
)
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
)
from receipt_dynamo.entities.receipt_validation_category import (
    ReceiptValidationCategory,
)
from receipt_dynamo.entities.receipt_validation_result import (
    ReceiptValidationResult,
)
from receipt_dynamo.entities.receipt_validation_summary import (
    ReceiptValidationSummary,
)
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_positive_int,
)


@dataclass
class ReceiptAnalysis:
    """
    Aggregates all types of analyses performed on a receipt.

    This class combines the different types of analyses that can be performed
    on a receipt into a single data structure, making it easier to retrieve
    all analyses at once.

    Attributes:
        image_id (str): UUID identifying the image containing the receipt
        receipt_id (int): ID of the receipt being analyzed
        label_analysis (ReceiptLabelAnalysis | None): Analysis of
            labels/fields in the receipt
        structure_analysis (ReceiptStructureAnalysis | None): Analysis of
            the structural layout
        line_item_analysis (ReceiptLineItemAnalysis | None): Analysis of
            line items (products, prices)
        validation_summary (ReceiptValidationSummary | None): Validation
            results and issues
        validation_categories (list[ReceiptValidationCategory]): Detailed
            validation by category
        validation_results (list[ReceiptValidationResult]): Individual
            validation results
        chatgpt_validations (list[ReceiptChatGPTValidation]): ChatGPT
            validation results
    """

    image_id: str
    receipt_id: int
    label_analysis: ReceiptLabelAnalysis | None = None
    structure_analysis: ReceiptStructureAnalysis | None = None
    line_item_analysis: ReceiptLineItemAnalysis | None = None
    validation_summary: ReceiptValidationSummary | None = None
    validation_categories: list[ReceiptValidationCategory] | None = None
    validation_results: list[ReceiptValidationResult] | None = None
    chatgpt_validations: list[ReceiptChatGPTValidation] | None = None

    def __post_init__(self):
        """Initialize empty lists for collection fields if they are None."""
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        self.validation_categories = list(self.validation_categories or [])
        self.validation_results = list(self.validation_results or [])
        self.chatgpt_validations = list(self.chatgpt_validations or [])

        collection_contracts = (
            (
                "validation_categories",
                self.validation_categories,
                ReceiptValidationCategory,
            ),
            (
                "validation_results",
                self.validation_results,
                ReceiptValidationResult,
            ),
            (
                "chatgpt_validations",
                self.chatgpt_validations,
                ReceiptChatGPTValidation,
            ),
        )
        for field_name, values, expected_type in collection_contracts:
            if not all(isinstance(value, expected_type) for value in values):
                raise ValueError(
                    f"{field_name} must contain {expected_type.__name__} objects"
                )

    def __repr__(self) -> str:
        """Return a string representation of the ReceiptAnalysis object."""
        available_analyses = []
        if self.label_analysis:
            available_analyses.append("label_analysis")
        if self.structure_analysis:
            available_analyses.append("structure_analysis")
        if self.line_item_analysis:
            available_analyses.append("line_item_analysis")
        if self.validation_summary:
            available_analyses.append("validation_summary")
        if self.validation_categories:
            available_analyses.append(
                f"validation_categories({len(self.validation_categories)})"
            )
        if self.validation_results:
            available_analyses.append(
                f"validation_results({len(self.validation_results)})"
            )
        if self.chatgpt_validations:
            available_analyses.append(
                f"chatgpt_validations({len(self.chatgpt_validations)})"
            )

        analyses_str = (
            ", ".join(available_analyses)
            if available_analyses
            else "no analyses"
        )

        return (
            f"ReceiptAnalysis(image_id={self.image_id}, "
            f"receipt_id={self.receipt_id}, "
            f"available={analyses_str})"
        )

from dataclasses import dataclass
from typing import List, Optional

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
        label_analysis (Optional[ReceiptLabelAnalysis]): Analysis of
            labels/fields in the receipt
        structure_analysis (Optional[ReceiptStructureAnalysis]): Analysis of
            the structural layout
        line_item_analysis (Optional[ReceiptLineItemAnalysis]): Analysis of
            line items (products, prices)
        validation_summary (Optional[ReceiptValidationSummary]): Validation
            results and issues
        validation_categories (List[ReceiptValidationCategory]): Detailed
            validation by category
        validation_results (List[ReceiptValidationResult]): Individual
            validation results
        chatgpt_validations (List[ReceiptChatGPTValidation]): ChatGPT
            validation results
    """

    image_id: str
    receipt_id: int
    label_analysis: Optional[ReceiptLabelAnalysis] = None
    structure_analysis: Optional[ReceiptStructureAnalysis] = None
    line_item_analysis: Optional[ReceiptLineItemAnalysis] = None
    validation_summary: Optional[ReceiptValidationSummary] = None
    validation_categories: Optional[List[ReceiptValidationCategory]] = None
    validation_results: Optional[List[ReceiptValidationResult]] = None
    chatgpt_validations: Optional[List[ReceiptChatGPTValidation]] = None

    def __post_init__(self):
        """Initialize empty lists for collection fields if they are None."""
        if self.validation_categories is None:
            self.validation_categories = []
        if self.validation_results is None:
            self.validation_results = []
        if self.chatgpt_validations is None:
            self.chatgpt_validations = []

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
            ", ".join(available_analyses) if available_analyses else "no analyses"
        )

        return (
            f"ReceiptAnalysis(image_id={self.image_id}, "
            f"receipt_id={self.receipt_id}, "
            f"available={analyses_str})"
        )

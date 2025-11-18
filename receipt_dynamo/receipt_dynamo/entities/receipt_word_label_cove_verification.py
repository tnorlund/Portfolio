"""ReceiptWordLabelCoVeVerification entity for storing CoVe verification chain data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_value,
    to_dynamodb_value,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptWordLabelCoVeVerification(DynamoDBEntity):
    """
    Stores the Chain of Verification (CoVe) verification chain for a ReceiptWordLabel.

    This entity captures the full verification process:
    1. Verification questions generated
    2. Answers to those questions
    3. Revision decisions
    4. Final validation outcome

    Attributes:
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        line_id (int): Number identifying the line containing the word.
        word_id (int): Number identifying the word.
        label (str): The label that was verified.
        verification_questions (List[Dict]): List of verification questions (from VerificationQuestionsResponse).
        verification_answers (List[Dict]): List of verification answers (from VerificationAnswersResponse).
        initial_label (str): Label before CoVe verification.
        final_label (str): Label after CoVe verification.
        revision_needed (bool): Whether revision was needed.
        revision_reasoning (Optional[str]): Reasoning for revision if needed.
        overall_assessment (str): Overall assessment from CoVe.
        cove_verified (bool): Whether CoVe completed successfully.
        verification_timestamp (str): ISO formatted timestamp when verification occurred.
        llm_model (str): LLM model used (e.g., "gpt-oss:120b").
        word_text (Optional[str]): The actual word text (for similarity matching).
        merchant_name (Optional[str]): Merchant name (for merchant-level template matching).
        can_be_reused_as_template (bool): Whether this verification can be reused as a template.
        template_applicability_score (float): How applicable this template is (0-1).
    """

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    verification_questions: List[Dict[str, Any]]
    verification_answers: List[Dict[str, Any]]
    initial_label: str
    final_label: str
    revision_needed: bool
    overall_assessment: str
    cove_verified: bool
    verification_timestamp: datetime | str
    llm_model: str
    revision_reasoning: Optional[str] = None
    word_text: Optional[str] = None
    merchant_name: Optional[str] = None
    can_be_reused_as_template: bool = False
    template_applicability_score: float = 0.0

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)
        validate_positive_int("line_id", self.line_id)
        validate_positive_int("word_id", self.word_id)

        if not isinstance(self.label, str):
            raise ValueError("label must be a string")
        if not self.label:
            raise ValueError("label cannot be empty")
        self.label = self.label.upper()

        if not isinstance(self.verification_questions, list):
            raise ValueError("verification_questions must be a list")
        if not isinstance(self.verification_answers, list):
            raise ValueError("verification_answers must be a list")

        if not isinstance(self.initial_label, str):
            raise ValueError("initial_label must be a string")
        self.initial_label = self.initial_label.upper()

        if not isinstance(self.final_label, str):
            raise ValueError("final_label must be a string")
        self.final_label = self.final_label.upper()

        if not isinstance(self.revision_needed, bool):
            raise ValueError("revision_needed must be a boolean")

        if not isinstance(self.overall_assessment, str):
            raise ValueError("overall_assessment must be a string")

        if not isinstance(self.cove_verified, bool):
            raise ValueError("cove_verified must be a boolean")

        if not isinstance(self.llm_model, str):
            raise ValueError("llm_model must be a string")

        # Convert datetime to string for storage
        if isinstance(self.verification_timestamp, datetime):
            self.verification_timestamp = self.verification_timestamp.isoformat()
        elif isinstance(self.verification_timestamp, str):
            # Validate it's a valid ISO format
            try:
                datetime.fromisoformat(self.verification_timestamp)
            except ValueError as e:
                raise ValueError(
                    "verification_timestamp string must be in ISO format"
                ) from e
        else:
            raise ValueError(
                "verification_timestamp must be a datetime object or a string"
            )

        if self.revision_reasoning is not None:
            if not isinstance(self.revision_reasoning, str):
                raise ValueError("revision_reasoning must be a string or None")

        if self.word_text is not None:
            if not isinstance(self.word_text, str):
                raise ValueError("word_text must be a string or None")

        if self.merchant_name is not None:
            if not isinstance(self.merchant_name, str):
                raise ValueError("merchant_name must be a string or None")

        if not isinstance(self.can_be_reused_as_template, bool):
            raise ValueError("can_be_reused_as_template must be a boolean")

        if not isinstance(self.template_applicability_score, (int, float)):
            raise ValueError("template_applicability_score must be a number")
        if not (0.0 <= self.template_applicability_score <= 1.0):
            raise ValueError(
                "template_applicability_score must be between 0.0 and 1.0"
            )

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the CoVe verification record.

        Returns:
            dict: The primary key for the CoVe verification record.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}#LABEL#{self.label}#COVE"
                )
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generate the GSI1 key for querying by label type + verification status.

        Returns:
            dict: GSI1 key for querying by label and verification status.
        """
        return {
            "GSI1PK": {"S": f"LABEL#{self.label}#COVE"},
            "GSI1SK": {
                "S": (
                    f"VERIFIED#{self.cove_verified}#{self.verification_timestamp}"
                )
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generate the GSI2 key for querying by merchant + label type (for template lookup).

        Returns:
            dict: GSI2 key for merchant-level template matching.
        """
        if not self.merchant_name:
            return {}

        merchant_normalized = self.merchant_name.strip().upper()
        return {
            "GSI2PK": {"S": f"MERCHANT#{merchant_normalized}#LABEL#{self.label}"},
            "GSI2SK": {
                "S": (
                    f"TEMPLATE#{self.can_be_reused_as_template}#{self.verification_timestamp}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptWordLabelCoVeVerification object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the object as a DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_WORD_LABEL_COVE_VERIFICATION"},
            "verification_questions": to_dynamodb_value(self.verification_questions),
            "verification_answers": to_dynamodb_value(self.verification_answers),
            "initial_label": {"S": self.initial_label},
            "final_label": {"S": self.final_label},
            "revision_needed": {"BOOL": self.revision_needed},
            "overall_assessment": {"S": self.overall_assessment},
            "cove_verified": {"BOOL": self.cove_verified},
            "verification_timestamp": {"S": self.verification_timestamp},
            "llm_model": {"S": self.llm_model},
            "can_be_reused_as_template": {"BOOL": self.can_be_reused_as_template},
            "template_applicability_score": {"N": str(self.template_applicability_score)},
        }

        # Add GSI2 if merchant_name is present
        if self.merchant_name:
            item.update(self.gsi2_key())

        # Optional fields
        if self.revision_reasoning is not None:
            item["revision_reasoning"] = {"S": self.revision_reasoning}
        else:
            item["revision_reasoning"] = {"NULL": True}

        if self.word_text is not None:
            item["word_text"] = {"S": self.word_text}
        else:
            item["word_text"] = {"NULL": True}

        if self.merchant_name is not None:
            item["merchant_name"] = {"S": self.merchant_name}
        else:
            item["merchant_name"] = {"NULL": True}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the object."""
        return (
            "ReceiptWordLabelCoVeVerification("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"label={_repr_str(self.label)}, "
            f"cove_verified={self.cove_verified}, "
            f"revision_needed={self.revision_needed}, "
            f"verification_timestamp={_repr_str(self.verification_timestamp)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the object's attributes."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "label", self.label
        yield "verification_questions", self.verification_questions
        yield "verification_answers", self.verification_answers
        yield "initial_label", self.initial_label
        yield "final_label", self.final_label
        yield "revision_needed", self.revision_needed
        yield "revision_reasoning", self.revision_reasoning
        yield "overall_assessment", self.overall_assessment
        yield "cove_verified", self.cove_verified
        yield "verification_timestamp", self.verification_timestamp
        yield "llm_model", self.llm_model
        yield "word_text", self.word_text
        yield "merchant_name", self.merchant_name
        yield "can_be_reused_as_template", self.can_be_reused_as_template
        yield "template_applicability_score", self.template_applicability_score


def item_to_receipt_word_label_cove_verification(
    item: Dict[str, Any],
) -> ReceiptWordLabelCoVeVerification:
    """Converts a DynamoDB item to a ReceiptWordLabelCoVeVerification object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWordLabelCoVeVerification: The object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {"PK", "SK", "verification_questions", "verification_answers"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Invalid item format - missing keys: {missing_keys}")

    try:
        sk_parts = item["SK"]["S"].split("#")
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(sk_parts[1])
        line_id = int(sk_parts[3])
        word_id = int(sk_parts[5])
        label = sk_parts[7]

        # Parse verification questions using utility function
        verification_questions = []
        if "verification_questions" in item:
            parsed_questions = parse_dynamodb_value(item["verification_questions"])
            if isinstance(parsed_questions, list):
                verification_questions = parsed_questions
            else:
                verification_questions = []

        # Parse verification answers using utility function
        verification_answers = []
        if "verification_answers" in item:
            parsed_answers = parse_dynamodb_value(item["verification_answers"])
            if isinstance(parsed_answers, list):
                verification_answers = parsed_answers
            else:
                verification_answers = []

        initial_label = item.get("initial_label", {}).get("S", label)
        final_label = item.get("final_label", {}).get("S", label)
        revision_needed = item.get("revision_needed", {}).get("BOOL", False)
        overall_assessment = item.get("overall_assessment", {}).get("S", "")
        cove_verified = item.get("cove_verified", {}).get("BOOL", False)
        verification_timestamp = item.get("verification_timestamp", {}).get("S", "")
        llm_model = item.get("llm_model", {}).get("S", "")

        revision_reasoning = None
        if "revision_reasoning" in item:
            if "NULL" not in item["revision_reasoning"]:
                revision_reasoning = item["revision_reasoning"].get("S")

        word_text = None
        if "word_text" in item:
            if "NULL" not in item["word_text"]:
                word_text = item["word_text"].get("S")

        merchant_name = None
        if "merchant_name" in item:
            if "NULL" not in item["merchant_name"]:
                merchant_name = item["merchant_name"].get("S")

        can_be_reused_as_template = item.get("can_be_reused_as_template", {}).get("BOOL", False)
        template_applicability_score = float(item.get("template_applicability_score", {}).get("N", "0.0"))

        return ReceiptWordLabelCoVeVerification(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
            verification_questions=verification_questions,
            verification_answers=verification_answers,
            initial_label=initial_label,
            final_label=final_label,
            revision_needed=revision_needed,
            revision_reasoning=revision_reasoning,
            overall_assessment=overall_assessment,
            cove_verified=cove_verified,
            verification_timestamp=verification_timestamp,
            llm_model=llm_model,
            word_text=word_text,
            merchant_name=merchant_name,
            can_be_reused_as_template=can_be_reused_as_template,
            template_applicability_score=template_applicability_score,
        )
    except Exception as e:
        raise ValueError(
            f"Error converting item to ReceiptWordLabelCoVeVerification: {e}"
        ) from e


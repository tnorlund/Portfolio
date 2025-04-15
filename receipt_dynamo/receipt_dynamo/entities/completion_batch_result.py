from datetime import datetime
from typing import Any, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid
from receipt_dynamo.constants import ValidationStatus


class CompletionBatchResult:
    """
    A completion batch result is a result of a completion batch.
    """

    def __init__(
        self,
        batch_id: str,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        original_label: str,
        gpt_suggested_label: str,
        label_confidence: float,
        label_changed: bool,
        status: str,
        validated_at: datetime,
        reasoning: str,
        raw_prompt: str,
        raw_response: str,
        label_target: Optional[str] = None,  # Added label_target as optional
    ):
        assert_valid_uuid(batch_id)
        self.batch_id = batch_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        self.receipt_id = receipt_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        self.line_id = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        self.word_id = word_id

        if not isinstance(original_label, str):
            raise ValueError("original_label must be a string")
        self.original_label = original_label

        if not isinstance(gpt_suggested_label, str):
            raise ValueError("gpt_suggested_label must be a string")
        self.gpt_suggested_label = gpt_suggested_label

        if not isinstance(label_confidence, float):
            raise ValueError("label_confidence must be a float")
        self.label_confidence = label_confidence

        if not isinstance(label_changed, bool):
            raise ValueError("label_changed must be a boolean")
        self.label_changed = label_changed

        if not isinstance(status, str):
            raise ValueError("status must be a string")
        if not status in [s.value for s in ValidationStatus]:
            raise ValueError(
                f"status must be one of: {', '.join(status.value for status in ValidationStatus)}"
            )
        self.status = status

        if not isinstance(validated_at, datetime):
            raise ValueError("validated_at must be a datetime object")
        self.validated_at = validated_at

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        self.reasoning = reasoning

        if not isinstance(raw_prompt, str):
            raise ValueError("raw_prompt must be a string")
        self.raw_prompt = raw_prompt

        if not isinstance(raw_response, str):
            raise ValueError("raw_response must be a string")
        self.raw_response = raw_response

        if label_target is not None and not isinstance(label_target, str):
            raise ValueError("label_target must be a string if provided")
        self.label_target = label_target  # Initialize label_target

    def key(self) -> dict:
        """
        The key for the completion batch result is a composite key that consists of the batch id and the receipt id, line id, and word id.

        PK: BATCH#<batch_id>
        SK: RESULT#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>

        Returns:
            dict: The key for the completion batch result.
        """
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {
                "S": f"RESULT#RECEIPT#{self.receipt_id}#LINE#{self.line_id}#WORD#{self.word_id}#LABEL#{self.original_label}"
            },
        }

    def gsi1_key(self) -> dict:
        return {
            "GSI1PK": {"S": f"LABEL_TARGET#{self.label_target or 'unknown'}"},
            "GSI1SK": {"S": f"STATUS#{self.status}"},
        }

    def gsi2_key(self) -> dict:
        return {
            "GSI2PK": {"S": f"BATCH#{self.batch_id}"},
            "GSI2SK": {"S": f"STATUS#{self.status}"},
        }

    def gsi3_key(self) -> dict:
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id}"
            },
            "GSI3SK": {"S": f"BATCH#{self.batch_id}#STATUS#{self.status}"},
        }

    def to_item(self) -> dict:
        """
        Converts the completion batch result to an item for DynamoDB.
        """
        item = {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "COMPLETION_BATCH_RESULT"},
            "original_label": {"S": self.original_label},
            "gpt_suggested_label": {"S": self.gpt_suggested_label},
            "label_confidence": {"N": str(self.label_confidence)},
            "label_changed": {"BOOL": self.label_changed},
            "status": {"S": self.status},
            "validated_at": {"S": self.validated_at.isoformat()},
            "reasoning": {"S": self.reasoning},
            "raw_prompt": {"S": self.raw_prompt},
            "raw_response": {"S": self.raw_response},
        }
        if self.label_target is not None:
            item["label_target"] = {
                "S": self.label_target
            }  # Include label_target if not None
        return item

    def __repr__(self) -> str:
        """
        Returns a string representation of the completion batch result.
        """
        return (
            "CompletionBatchResult("
            f"batch_id={_repr_str(self.batch_id)}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={_repr_str(self.receipt_id)}, "
            f"line_id={_repr_str(self.line_id)}, "
            f"word_id={_repr_str(self.word_id)}, "
            f"original_label={_repr_str(self.original_label)}, "
            f"gpt_suggested_label={_repr_str(self.gpt_suggested_label)}, "
            f"label_confidence={self.label_confidence}, "
            f"label_changed={self.label_changed}, "
            f"status={_repr_str(self.status)}, "
            f"validated_at={_repr_str(self.validated_at)}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"raw_prompt={_repr_str(self.raw_prompt)}, "
            f"raw_response={_repr_str(self.raw_response)}"
            f", label_target={_repr_str(self.label_target)}"  # Include label_target in repr
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "batch_id", self.batch_id
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "original_label", self.original_label
        yield "gpt_suggested_label", self.gpt_suggested_label
        yield "label_confidence", self.label_confidence
        yield "label_changed", self.label_changed
        yield "status", self.status
        yield "validated_at", self.validated_at
        yield "reasoning", self.reasoning
        yield "raw_prompt", self.raw_prompt
        yield "raw_response", self.raw_response
        yield "label_target", self.label_target  # Yield label_target

    def __eq__(self, other) -> bool:
        """
        Determines whether two completion batch result objects are equal.
        """
        if not isinstance(other, CompletionBatchResult):
            return False
        return (
            self.batch_id == other.batch_id
            and self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.original_label == other.original_label
            and self.gpt_suggested_label == other.gpt_suggested_label
            and self.label_confidence == other.label_confidence
            and self.label_changed == other.label_changed
            and self.status == other.status
            and self.validated_at == other.validated_at
            and self.reasoning == other.reasoning
            and self.raw_prompt == other.raw_prompt
            and self.raw_response == other.raw_response
            and self.label_target == other.label_target  # Compare label_target
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.batch_id,
                self.image_id,
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.original_label,
                self.gpt_suggested_label,
                self.label_confidence,
                self.label_changed,
                self.status,
                self.validated_at,
                self.reasoning,
                self.raw_prompt,
                self.raw_response,
                self.label_target,  # Include label_target in hash
            )
        )


def itemToCompletionBatchResult(item: dict) -> CompletionBatchResult:
    """
    Converts an item from DynamoDB to a CompletionBatchResult object.
    """
    required_keys = {
        "PK",
        "SK",
        "original_label",
        "gpt_suggested_label",
        "label_confidence",
        "label_changed",
        "status",
        "validated_at",
        "reasoning",
        "raw_prompt",
        "raw_response",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        batch_id = item["PK"]["S"].split("#")[1]  # From PK="BATCH#{batch_id}"
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[2])
        line_id = int(sk_parts[4])
        word_id = int(sk_parts[6])
        original_label = sk_parts[8]
        gpt_suggested_label = item["gpt_suggested_label"]["S"]
        label_confidence = float(item["label_confidence"]["N"])
        label_changed = item["label_changed"]["BOOL"]
        status = item["status"]["S"]
        validated_at = datetime.fromisoformat(item["validated_at"]["S"])
        reasoning = item["reasoning"]["S"]
        raw_prompt = item["raw_prompt"]["S"]
        raw_response = item["raw_response"]["S"]
        label_target = item.get("label_target", {}).get(
            "S"
        )  # Get label_target if present
        image_id = item["GSI3PK"]["S"].split("#")[1]
        return CompletionBatchResult(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            original_label=original_label,
            gpt_suggested_label=gpt_suggested_label,
            label_confidence=label_confidence,
            label_changed=label_changed,
            status=status,
            validated_at=validated_at,
            reasoning=reasoning,
            raw_prompt=raw_prompt,
            raw_response=raw_response,
            label_target=label_target,  # Pass label_target to constructor
        )
    except Exception as e:
        raise ValueError(
            f"Error converting item to CompletionBatchResult: {e}"
        )

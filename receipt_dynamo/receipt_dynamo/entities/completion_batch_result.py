from datetime import datetime
from typing import Any, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid
from receipt_dynamo.constants import ValidationStatus, PassNumber


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
        status: str,
        validated_at: datetime,
        reasoning: str,
        raw_prompt: str,
        raw_response: str,
        is_valid: bool,
        vector_id: str,
        pass_number: PassNumber | str,
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

        if not isinstance(is_valid, bool):
            raise ValueError("is_valid must be a boolean")
        self.is_valid = is_valid

        if not isinstance(vector_id, str):
            raise ValueError("vector_id must be a string")
        self.vector_id = vector_id

        if isinstance(pass_number, PassNumber):
            pass_number_str = pass_number.value
        elif isinstance(pass_number, str):
            pass_number_str = pass_number
        else:
            raise ValueError("pass_number must be a PassNumber or a string")
        valid_pass_numbers = [p.value for p in PassNumber]
        if pass_number_str not in valid_pass_numbers:
            raise ValueError(
                f"pass_number must be one of: {', '.join(valid_pass_numbers)}\n"
                f"got: {pass_number_str}"
            )
        self.pass_number = pass_number_str

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
            "GSI1PK": {"S": f"LABEL#{self.original_label}"},
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
        return {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "COMPLETION_BATCH_RESULT"},
            "original_label": {"S": self.original_label},
            "gpt_suggested_label": {"S": self.gpt_suggested_label},
            "status": {"S": self.status},
            "validated_at": {"S": self.validated_at.isoformat()},
            "reasoning": {"S": self.reasoning},
            "raw_prompt": {"S": self.raw_prompt},
            "raw_response": {"S": self.raw_response},
            "is_valid": {"BOOL": self.is_valid},
            "vector_id": {"S": self.vector_id},
            "pass_number": {"S": self.pass_number},
        }

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
            f"status={_repr_str(self.status)}, "
            f"validated_at={_repr_str(self.validated_at)}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"raw_prompt={_repr_str(self.raw_prompt)}, "
            f"raw_response={_repr_str(self.raw_response)}, "
            f"is_valid={_repr_str(self.is_valid)}, "
            f"vector_id={_repr_str(self.vector_id)}, "
            f"pass_number={_repr_str(self.pass_number)}"
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
        yield "status", self.status
        yield "validated_at", self.validated_at
        yield "reasoning", self.reasoning
        yield "raw_prompt", self.raw_prompt
        yield "raw_response", self.raw_response
        yield "is_valid", self.is_valid
        yield "vector_id", self.vector_id
        yield "pass_number", self.pass_number

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
            and self.status == other.status
            and self.validated_at == other.validated_at
            and self.reasoning == other.reasoning
            and self.raw_prompt == other.raw_prompt
            and self.raw_response == other.raw_response
            and self.is_valid == other.is_valid
            and self.vector_id == other.vector_id
            and self.pass_number == other.pass_number
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
                self.status,
                self.validated_at,
                self.reasoning,
                self.raw_prompt,
                self.raw_response,
                self.is_valid,
                self.vector_id,
                self.pass_number,
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
        "status",
        "validated_at",
        "reasoning",
        "raw_prompt",
        "raw_response",
        "is_valid",
        "vector_id",
        "pass_number",
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
        status = item["status"]["S"]
        validated_at = datetime.fromisoformat(item["validated_at"]["S"])
        reasoning = item["reasoning"]["S"]
        raw_prompt = item["raw_prompt"]["S"]
        raw_response = item["raw_response"]["S"]
        is_valid = item["is_valid"]["BOOL"]
        vector_id = item["vector_id"]["S"]
        pass_number = item["pass_number"]["S"]
        image_id = item["GSI3PK"]["S"].split("#")[1]
        return CompletionBatchResult(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            original_label=original_label,
            gpt_suggested_label=gpt_suggested_label,
            status=status,
            validated_at=validated_at,
            reasoning=reasoning,
            raw_prompt=raw_prompt,
            raw_response=raw_response,
            is_valid=is_valid,
            vector_id=vector_id,
            pass_number=pass_number,
        )
    except Exception as e:
        raise ValueError(
            f"Error converting item to CompletionBatchResult: {e}"
        )

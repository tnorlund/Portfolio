from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import item_to_receipt_line
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

    from receipt_dynamo.data.base_operations import (
        BatchGetItemInputTypeDef,
    )

CHUNK_SIZE = 25


class _ReceiptLine(FlattenedStandardMixin):
    """
    A class used to represent a ReceiptLine in the database
    (similar to _line.py).

    Methods
    -------
    add_receipt_line(line: ReceiptLine)
        Adds a receipt-line to the database.
    add_receipt_lines(lines: list[ReceiptLine])
        Adds multiple receipt-lines in batch.
    update_receipt_line(line: ReceiptLine)
        Updates an existing receipt-line.
    delete_receipt_line(receipt_id: int, image_id: str, line_id: int)
        Deletes a specific receipt-line by IDs.
    delete_receipt_lines(lines: list[ReceiptLine])
        Deletes multiple receipt-lines in batch.
    get_receipt_line(receipt_id: int, image_id: str, line_id: int)
        -> ReceiptLine
        Retrieves a single receipt-line by IDs.
    list_receipt_lines() -> list[ReceiptLine]
        Returns all ReceiptLines from the table.
    list_receipt_lines_from_receipt(receipt_id: int, image_id: str)
        -> list[ReceiptLine]
        Returns all lines under a specific receipt/image.
    """

    @handle_dynamodb_errors("add_receipt_line")
    def add_receipt_line(self, line: ReceiptLine) -> None:
        """Adds a single ReceiptLine to DynamoDB."""
        self._validate_entity(line, ReceiptLine, "line")
        self._add_entity(line)

    def add_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Adds multiple ReceiptLines to DynamoDB."""
        self._validate_entity_list(lines, ReceiptLine, "lines")
        self._add_entities(lines)

    @handle_dynamodb_errors("update_receipt_line")
    def update_receipt_line(self, line: ReceiptLine) -> None:
        """Updates an existing ReceiptLine in DynamoDB."""
        self._validate_entity(line, ReceiptLine, "line")
        self._update_entity(line)

    @handle_dynamodb_errors("update_receipt_lines")
    def update_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Updates multiple existing ReceiptLines in DynamoDB."""
        self._validate_entity_list(lines, ReceiptLine, "lines")
        self._update_entities(lines, ReceiptLine, "lines")

    @handle_dynamodb_errors("delete_receipt_line")
    def delete_receipt_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> None:
        """Deletes a single ReceiptLine by IDs."""
        # Direct key-based deletion is more efficient than creating
        # dummy objects
        key = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
        }
        self._client.delete_item(
            TableName=self.table_name,
            Key=key,
            ConditionExpression="attribute_exists(PK)",
        )

    def delete_receipt_lines(self, lines: list[ReceiptLine]) -> None:
        """Deletes multiple ReceiptLines in batch."""
        self._validate_entity_list(lines, ReceiptLine, "lines")
        self._delete_entities_batch(lines)

    @handle_dynamodb_errors("get_receipt_line")
    def get_receipt_line(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> ReceiptLine:
        """Retrieves a single ReceiptLine by IDs."""
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}",
            entity_class=ReceiptLine,
            converter_func=item_to_receipt_line,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptLine with image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id} not found"
            )

        return result

    @handle_dynamodb_errors("get_receipt_lines_by_indices")
    def get_receipt_lines_by_indices(
        self, indices: list[tuple[str, int, int]]
    ) -> list[ReceiptLine]:
        """Retrieves multiple ReceiptLines by their indices."""
        if indices is None:
            raise EntityValidationError("indices cannot be None")
        if not isinstance(indices, list):
            raise EntityValidationError("indices must be a list of tuples.")
        if not all(isinstance(index, tuple) for index in indices):
            raise EntityValidationError("indices must be a list of tuples.")

        for index in indices:
            if len(index) != 3:
                raise EntityValidationError(
                    "indices must be a list of tuples with 3 elements."
                )
            if not isinstance(index[0], str):
                raise EntityValidationError(
                    "First element of tuple must be a string."
                )
            assert_valid_uuid(index[0])
            if not isinstance(index[1], int):
                raise EntityValidationError(
                    "Second element of tuple must be an integer."
                )
            if not isinstance(index[2], int):
                raise EntityValidationError(
                    "Third element of tuple must be an integer."
                )

        # Assemble the keys
        keys = []
        for index in indices:
            keys.append(
                {
                    "PK": {"S": f"IMAGE#{index[0]}"},
                    "SK": {"S": f"RECEIPT#{index[1]:05d}#LINE#{index[2]:05d}"},
                }
            )

        # Get the receipt lines
        return self.get_receipt_lines_by_keys(keys)

    @handle_dynamodb_errors("get_receipt_lines_by_keys")
    def get_receipt_lines_by_keys(self, keys: list[dict]) -> list[ReceiptLine]:
        """Retrieves multiple ReceiptLines by their keys."""
        if not keys:
            raise EntityValidationError("keys cannot be None or empty")
        if not isinstance(keys, list):
            raise EntityValidationError("keys must be a list of dictionaries.")

        # Validate all keys
        for key in keys:
            self._validate_receipt_line_key(key)

        # Batch get items in chunks of 25 (DynamoDB limit)
        results: List[Dict[str, Any]] = []
        for i in range(0, len(keys), CHUNK_SIZE):
            chunk = keys[i : i + CHUNK_SIZE]
            request: BatchGetItemInputTypeDef = {
                "RequestItems": {self.table_name: {"Keys": chunk}}
            }

            # Perform batch get with retry for unprocessed keys
            response = self._client.batch_get_item(**request)
            results.extend(response["Responses"].get(self.table_name, []))

            # Handle unprocessed keys
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name, {}).get("Keys"):
                response = self._client.batch_get_item(
                    RequestItems=unprocessed
                )
                results.extend(response["Responses"].get(self.table_name, []))
                unprocessed = response.get("UnprocessedKeys", {})

        return [item_to_receipt_line(item) for item in results]

    def _validate_receipt_line_key(self, key: dict) -> None:
        """Validates a single ReceiptLine key structure."""
        if not isinstance(key, dict):
            raise EntityValidationError("Each key must be a dictionary")
        if not {"PK", "SK"}.issubset(key.keys()):
            raise EntityValidationError("keys must contain 'PK' and 'SK'")

        pk = key.get("PK", {}).get("S", "")
        sk = key.get("SK", {}).get("S", "")

        if not pk.startswith("IMAGE#"):
            raise EntityValidationError("PK must start with 'IMAGE#'")
        if not sk.startswith("RECEIPT#"):
            raise EntityValidationError("SK must start with 'RECEIPT#'")

        # Validate SK format: RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
        sk_parts = sk.split("#")
        if len(sk_parts) < 4:
            raise EntityValidationError("Invalid SK format")
        if sk_parts[2] != "LINE":
            raise EntityValidationError("SK must contain 'LINE'")
        if len(sk_parts[1]) != 5 or not sk_parts[1].isdigit():
            raise EntityValidationError("SK must contain a 5-digit receipt ID")
        if len(sk_parts[3]) != 5 or not sk_parts[3].isdigit():
            raise EntityValidationError("SK must contain a 5-digit line ID")

    @handle_dynamodb_errors("list_receipt_lines")
    def list_receipt_lines(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[list[ReceiptLine], Optional[Dict[str, Any]]]:
        """Returns all ReceiptLines from the table."""
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )
        return self._query_by_type(
            entity_type="RECEIPT_LINE",
            converter_func=item_to_receipt_line,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    def list_receipt_lines_by_embedding_status(
        self, embedding_status: EmbeddingStatus | str
    ) -> list[ReceiptLine]:
        """Returns all ReceiptLines from the table with a given embedding
        status."""
        if isinstance(embedding_status, EmbeddingStatus):
            status_str = embedding_status.value
        elif isinstance(embedding_status, str):
            status_str = embedding_status
        else:
            raise EntityValidationError(
                "embedding_status must be an instance of EmbeddingStatus "
                "or a string"
            )

        if status_str not in [status.value for status in EmbeddingStatus]:
            raise EntityValidationError(
                "embedding_status must be a valid EmbeddingStatus"
            )

        results, _ = self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :status",
            expression_attribute_names=None,
            expression_attribute_values={
                ":status": {"S": f"EMBEDDING_STATUS#{status_str}"}
            },
            converter_func=item_to_receipt_line,
        )

        return results

    def list_receipt_lines_from_receipt(
        self, receipt_id: int, image_id: str
    ) -> list[ReceiptLine]:
        """Returns all lines under a specific receipt/image."""
        results, _ = self._query_by_parent(
            parent_key_prefix=f"IMAGE#{image_id}",
            child_key_prefix=f"RECEIPT#{receipt_id:05d}#LINE#",
            converter_func=item_to_receipt_line,
        )
        return results

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
    item_to_receipt_line_embedding,
)


class _ReceiptLineEmbedding(FlattenedStandardMixin):
    """
    A class providing methods to interact with "ReceiptLineEmbedding"
    entities in DynamoDB — the visual-row vector items read by the
    ``line-embeddings`` vector index.

    This class is typically used within a DynamoClient to access and
    manage receipt line embedding records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_line_embedding(embedding: ReceiptLineEmbedding):
        Adds a single ReceiptLineEmbedding.
    add_receipt_line_embeddings(embeddings: list[ReceiptLineEmbedding]):
        Adds multiple ReceiptLineEmbeddings.
    delete_receipt_line_embeddings(embeddings: list[ReceiptLineEmbedding]):
        Deletes multiple ReceiptLineEmbeddings.
    get_receipt_line_embedding(image_id, receipt_id, line_id)
        -> ReceiptLineEmbedding:
        Retrieves a single ReceiptLineEmbedding by IDs.
    list_receipt_line_embeddings_from_receipt(image_id, receipt_id)
        -> list[ReceiptLineEmbedding]:
        Retrieves all ReceiptLineEmbeddings for a given receipt.
    list_receipt_line_embeddings(...)
        -> tuple[list[ReceiptLineEmbedding], dict | None]:
        Returns ReceiptLineEmbeddings by TYPE with pagination (backfill
        audits).
    """

    @handle_dynamodb_errors("add_receipt_line_embedding")
    def add_receipt_line_embedding(
        self, embedding: ReceiptLineEmbedding
    ) -> None:
        """
        Adds a single ReceiptLineEmbedding to DynamoDB.

        Parameters
        ----------
        embedding : ReceiptLineEmbedding
            The ReceiptLineEmbedding to add.

        Raises
        ------
        ValueError
            If the embedding already exists.
        """
        self._validate_entity(embedding, ReceiptLineEmbedding, "embedding")
        self._add_entity(
            embedding, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipt_line_embeddings")
    def add_receipt_line_embeddings(
        self, embeddings: list[ReceiptLineEmbedding]
    ) -> None:
        """
        Adds multiple ReceiptLineEmbeddings to DynamoDB in batches.

        Parameters
        ----------
        embeddings : list[ReceiptLineEmbedding]
            The ReceiptLineEmbeddings to add.

        Raises
        ------
        ValueError
            If embeddings is invalid.
        """
        self._validate_entity_list(
            embeddings, ReceiptLineEmbedding, "embeddings"
        )
        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=e.to_item()))
            for e in embeddings
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_receipt_line_embeddings")
    def delete_receipt_line_embeddings(
        self, embeddings: list[ReceiptLineEmbedding]
    ) -> None:
        """
        Deletes multiple ReceiptLineEmbeddings in batch.

        Parameters
        ----------
        embeddings : list[ReceiptLineEmbedding]
            The ReceiptLineEmbeddings to delete.

        Raises
        ------
        ValueError
            If unable to delete the embeddings.
        """
        self._validate_entity_list(
            embeddings, ReceiptLineEmbedding, "embeddings"
        )
        self._delete_entities(embeddings)

    @handle_dynamodb_errors("get_receipt_line_embedding")
    def get_receipt_line_embedding(
        self, image_id: str, receipt_id: int, line_id: int
    ) -> ReceiptLineEmbedding:
        """
        Retrieves a single ReceiptLineEmbedding by IDs.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.
        line_id : int
            The visual row's primary line ID.

        Returns
        -------
        ReceiptLineEmbedding
            The retrieved ReceiptLineEmbedding.

        Raises
        ------
        EntityNotFoundError
            If the embedding is not found.
        """
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#EMBEDDING"
            ),
            entity_class=ReceiptLineEmbedding,
            converter_func=item_to_receipt_line_embedding,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptLineEmbedding with image_id {image_id}, "
                f"receipt_id {receipt_id}, and line_id {line_id} not found"
            )

        return result

    @handle_dynamodb_errors("list_receipt_line_embeddings_from_receipt")
    def list_receipt_line_embeddings_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptLineEmbedding]:
        """
        Retrieves all ReceiptLineEmbeddings for a given receipt.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.

        Returns
        -------
        list[ReceiptLineEmbedding]
            List of ReceiptLineEmbeddings for the receipt.

        Raises
        ------
        ValueError
            If parameters are invalid or the query fails.
        """
        if image_id is None:
            raise EntityValidationError("image_id is required")
        if receipt_id is None:
            raise EntityValidationError("receipt_id is required")
        # The SK prefix also matches lines, words, letters, and word
        # embeddings in the same item collection; TYPE narrows to line
        # embedding items.
        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                ":t": {"S": "RECEIPT_LINE_EMBEDDING"},
            },
            converter_func=item_to_receipt_line_embedding,
            filter_expression="#t = :t",
        )
        return results

    @handle_dynamodb_errors("list_receipt_line_embeddings")
    def list_receipt_line_embeddings(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptLineEmbedding], dict | None]:
        """
        Returns ReceiptLineEmbeddings from the table with pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of items to return.
        last_evaluated_key : dict, optional
            Key to continue pagination from.

        Returns
        -------
        tuple[list[ReceiptLineEmbedding], dict | None]
            List of ReceiptLineEmbeddings and the last evaluated key.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_by_type(
            entity_type="RECEIPT_LINE_EMBEDDING",
            converter_func=item_to_receipt_line_embedding,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

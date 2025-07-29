from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import item_to_word
from receipt_dynamo.entities.util import assert_valid_uuid
from receipt_dynamo.entities.word import Word

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        BatchGetItemInputTypeDef,
        QueryInputTypeDef,
    )

# DynamoDB batch_write_item can only handle up to 25 items per call
# So let's chunk the items in groups of 25
CHUNK_SIZE = 25


class _Word(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to represent a Word in the database.

    Methods
    -------
    add_word(word: Word)
        Adds a word to the database.
    add_words(words: list[Word])
        Adds multiple words to the database.
    update_word(word: Word)
        Updates a word in the database.
    update_words(words: list[Word])
        Updates multiple words in the database.
    delete_word(image_id: str, line_id: int, word_id: int)
        Deletes a word from the database.
    delete_words(words: list[Word])
        Deletes multiple words from the database.
    get_word(image_id: str, line_id: int, word_id: int) -> Word
        Gets a word from the database.
    get_words(keys: list[dict]) -> list[Word]
        Gets multiple words from the database.
    list_words(
        limit: Optional[int] = None, last_evaluated_key: Optional[Dict] = None
    ) -> Tuple[list[Word], Optional[Dict[str, Any]]]
        Lists all words from the database.
    list_words_from_line(image_id: str, line_id: int) -> list[Word]
        Lists all words from a specific line.
    """

    @handle_dynamodb_errors("add_word")
    def add_word(self, word: Word):
        """Adds a word to the database

        Args:
            word (Word): The word to add to the database

        Raises:
            ValueError: When a word with the same ID already exists
        """
        self._validate_entity(word, Word, "word")
        self._add_entity(word)

    @handle_dynamodb_errors("add_words")
    def add_words(self, words: List[Word]):
        """Adds a list of words to the database

        Args:
            words (list[Word]): The words to add to the database

        Raises:
            ValueError: When validation fails or words cannot be added
        """
        self._validate_entity_list(words, Word, "words")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=word.to_item())
            )
            for word in words
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_word")
    def update_word(self, word: Word):
        """Updates a word in the database

        Args:
            word (Word): The word to update in the database

        Raises:
            ValueError: When a word with the same ID does not exist
        """
        self._validate_entity(word, Word, "word")
        self._update_entity(word)

    @handle_dynamodb_errors("update_words")
    def update_words(self, words: List[Word]):
        """
        Updates multiple Word items in the database.

        This method validates that the provided parameter is a list of Word
        instances. It uses DynamoDB's transact_write_items operation, which can
        handle up to 25 items per transaction.

        Parameters
        ----------
        words : list[Word]
            The list of Word objects to update.

        Raises
        ------
        ValueError: When given a bad parameter or words don't exist.
        Exception: For underlying DynamoDB errors.
        """
        self._update_entities(words, Word, "words")

    @handle_dynamodb_errors("delete_word")
    def delete_word(self, image_id: str, line_id: int, word_id: int):
        """Deletes a word from the database

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete
        """
        # Validate UUID
        assert_valid_uuid(image_id)
        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"LINE#{line_id:05d}#WORD#{word_id:05d}"},
            },
            ConditionExpression="attribute_exists(PK)",
        )

    @handle_dynamodb_errors("delete_words")
    def delete_words(self, words: List[Word]):
        """Deletes a list of words from the database"""
        self._validate_entity_list(words, Word, "words")

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=word.key)
            )
            for word in words
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_words_from_line")
    def delete_words_from_line(self, image_id: str, line_id: int):
        """Deletes all words from a line

        Args:
            image_id (str): The ID of the image the line belongs to
            line_id (int): The ID of the line to delete words from
        """
        words = self.list_words_from_line(image_id, line_id)
        self.delete_words(words)

    @handle_dynamodb_errors("get_word")
    def get_word(self, image_id: str, line_id: int, word_id: int) -> Word:
        """Gets a word from the database

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to get

        Returns:
            Word: The word object

        Raises:
            EntityNotFoundError: When the word is not found
        """
        assert_valid_uuid(image_id)
        
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"LINE#{line_id:05d}#WORD#{word_id:05d}",
            entity_class=Word,
            converter_func=item_to_word
        )
        
        if result is None:
            raise EntityNotFoundError(
                f"Word with image_id={image_id}, line_id={line_id}, word_id={word_id} not found"
            )
        
        return result

    @handle_dynamodb_errors("get_words")
    def get_words(self, keys: List[Dict]) -> List[Word]:
        """Get a list of words using a list of keys"""
        # Check the validity of the keys
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("Keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("LINE#"):
                raise ValueError("SK must start with 'LINE#'")
            if not key["SK"]["S"].split("#")[-2] == "WORD":
                raise ValueError("SK must contain 'WORD'")
        results = []

        # Split keys into chunks of up to 100
        for i in range(0, len(keys), CHUNK_SIZE):
            chunk = keys[i : i + CHUNK_SIZE]

            # Prepare parameters for BatchGetItem
            request: BatchGetItemInputTypeDef = {
                "RequestItems": {self.table_name: {"Keys": chunk}}
            }

            # Perform BatchGet
            response = self._client.batch_get_item(**request)

            # Combine all found items
            batch_items = response["Responses"].get(self.table_name, [])
            results.extend(batch_items)

            # Retry unprocessed keys if any
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name, {}).get(
                "Keys"
            ):  # type: ignore[call-overload]
                response = self._client.batch_get_item(
                    RequestItems=unprocessed
                )
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})

        return [item_to_word(result) for result in results]

    @handle_dynamodb_errors("list_words")
    def list_words(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Word], Optional[Dict[str, Any]]]:
        """Lists all words from the database

        Args:
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination

        Returns:
            Tuple of words list and last evaluated key for pagination
        """
        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={":val": {"S": "WORD"}},
            converter_func=item_to_word,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    @handle_dynamodb_errors("list_words_from_line")
    def list_words_from_line(self, image_id: str, line_id: int) -> List[Word]:
        """List all words from a specific line in an image.

        Args:
            image_id: The UUID of the image
            line_id: The ID of the line

        Returns:
            List of Word objects from the specified line
        """
        words, _ = self._query_entities(
            index_name=None,  # Main table query
            key_condition_expression="PK = :pkVal AND begins_with(SK, :skPrefix)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {"S": f"LINE#{line_id:05d}#WORD#"},
            },
            converter_func=item_to_word,
            limit=None,
            last_evaluated_key=None
        )
        return words

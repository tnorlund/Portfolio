from typing import Any, Dict

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.base_operations.shared_utils import (
    validate_batch_get_keys,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
)
from receipt_dynamo.entities import item_to_letter
from receipt_dynamo.entities.letter import Letter
from receipt_dynamo.entities.util import assert_valid_uuid


class _Letter(FlattenedStandardMixin):
    """
    A class used to represent a Letter in the database.

    Methods
    -------
    add_letter(letter: Letter)
        Adds a letter to the database.
    add_letters(letters: list[Letter])
        Adds multiple letters to the database.
    update_letter(letter: Letter)
        Updates a letter in the database.
    update_letters(letters: list[Letter])
        Updates multiple letters in the database.
    delete_letter(image_id: str, line_id: int, word_id: int, letter_id: int)
        Deletes a letter from the database.
    delete_letters(letters: list[Letter])
        Deletes multiple letters from the database.
    get_letter(image_id: str, line_id: int, word_id: int, letter_id: int)
        -> Letter
        Gets a letter from the database.
    get_letters(keys: list[dict]) -> list[Letter]
        Gets multiple letters from the database.
    list_letters(limit: int | None = None, last_evaluated_key:
        Dict | None = None) -> tuple[list[Letter], dict[str, Any] | None]
        Lists all letters from the database.
    list_letters_from_word(image_id: str, line_id: int, word_id: int)
        -> list[Letter]
        Lists all letters from a specific word.
    """

    @handle_dynamodb_errors("add_letter")
    def add_letter(self, letter: Letter):
        """Adds a letter to the database

        Args:
            letter (Letter): The letter to add to the database

        Raises:
            ValueError: When a letter with the same ID already exists
        """
        self._validate_entity(letter, Letter, "letter")
        self._add_entity(
            letter, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_letters")
    def add_letters(self, letters: list[Letter]):
        """Adds a list of letters to the database

        Args:
            letters (list[Letter]): The letters to add to the database

        Raises:
            ValueError: When validation fails or letters cannot be added
        """
        self._validate_entity_list(letters, Letter, "letters")
        self._add_entities(letters, Letter, "letters")

    @handle_dynamodb_errors("update_letter")
    def update_letter(self, letter: Letter):
        """Updates a letter in the database

        Args:
            letter (Letter): The letter to update in the database

        Raises:
            ValueError: When the letter does not exist
        """
        self._validate_entity(letter, Letter, "letter")
        self._update_entity(
            letter, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_letters")
    def update_letters(self, letters: list[Letter]):
        """
        Updates multiple Letter items in the database.

        This method validates that the provided parameter is a list of Letter
        instances. It uses DynamoDB's transact_write_items operation, which can
        handle up to 25 items per transaction.

        Parameters
        ----------
        letters : list[Letter]
            The list of Letter objects to update.

        Raises
        ------
        ValueError: When given a bad parameter or letters don't exist.
        Exception: For underlying DynamoDB errors.
        """
        self._update_entities(letters, Letter, "letters")

    @handle_dynamodb_errors("delete_letter")
    def delete_letter(
        self, image_id: str, line_id: int, word_id: int, letter_id: int
    ):
        """Deletes a letter from the database

        Args:
            image_id (str): The UUID of the image the letter belongs to
            line_id (int): The ID of the line the letter belongs to
            word_id (int): The ID of the word the letter belongs to
            letter_id (int): The ID of the letter to delete
        """
        # Validate UUID
        assert_valid_uuid(image_id)
        # Create a temporary Letter object with just the keys for deletion
        from receipt_dynamo.data.base_operations.shared_utils import (
            DEFAULT_GEOMETRY_FIELDS,
        )

        temp_letter = Letter(
            image_id=image_id,
            line_id=line_id,
            word_id=word_id,
            letter_id=letter_id,
            text="X",  # Single character required
            **DEFAULT_GEOMETRY_FIELDS,
        )
        self._delete_entity(
            temp_letter, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_letters")
    def delete_letters(self, letters: list[Letter]):
        """Deletes a list of letters from the database

        Args:
            letters (list[Letter]): The letters to delete
        """
        self._delete_entities(letters)

    @handle_dynamodb_errors("delete_letters_from_word")
    def delete_letters_from_word(
        self, image_id: str, line_id: int, word_id: int
    ):
        """Deletes all letters from a word

        Args:
            image_id (str): The UUID of the image the word belongs to
            line_id (int): The ID of the line the word belongs to
            word_id (int): The ID of the word to delete letters from
        """
        letters = self.list_letters_from_word(image_id, line_id, word_id)
        if letters:
            self.delete_letters(letters)

    @handle_dynamodb_errors("get_letter")
    def get_letter(
        self, image_id: str, line_id: int, word_id: int, letter_id: int
    ) -> Letter:
        """Gets a letter from the database

        Args:
            image_id (str): The UUID of the image the letter belongs to
            line_id (int): The ID of the line the letter belongs to
            word_id (int): The ID of the word the letter belongs to
            letter_id (int): The ID of the letter to get

        Returns:
            Letter: The letter object

        Raises:
            EntityNotFoundError: When the letter is not found
        """
        assert_valid_uuid(image_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(
                f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}"
            ),
            entity_class=Letter,
            converter_func=item_to_letter,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Letter with image_id={image_id}, line_id={line_id}, "
                f"word_id={word_id}, letter_id={letter_id} not found"
            )

        return result

    @handle_dynamodb_errors("get_letters")
    def get_letters(self, keys: list[Dict]) -> list[Letter]:
        """Get a list of letters using a list of keys."""
        validate_batch_get_keys(keys, "LETTER")
        results = self._batch_get_items(keys)
        return [item_to_letter(item) for item in results]

    @handle_dynamodb_errors("list_letters")
    def list_letters(
        self,
        limit: int | None = None,
        last_evaluated_key: Dict | None = None,
    ) -> tuple[list[Letter], dict[str, Any] | None]:
        """Lists all letters in the database

        Args:
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination

        Returns:
            Tuple of letters list and last evaluated key for pagination
        """
        return self._query_by_type(
            entity_type="LETTER",
            converter_func=item_to_letter,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_letters_from_word")
    def list_letters_from_word(
        self, image_id: str, line_id: int, word_id: int
    ) -> list[Letter]:
        """List all letters from a specific word.

        Args:
            image_id: The UUID of the image
            line_id: The ID of the line
            word_id: The ID of the word

        Returns:
            List of Letter objects from the specified word
        """
        # For letters, we need to query by a composite parent
        # (IMAGE + LINE + WORD). Since QueryByParentMixin expects a single
        # parent, we'll use the
        # original query
        letters, _ = self._query_entities(
            index_name=None,  # Main table query
            key_condition_expression=(
                "PK = :pkVal AND begins_with(SK, :skPrefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {
                    "S": f"LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#"
                },
            },
            converter_func=item_to_letter,
            limit=None,
            last_evaluated_key=None,
        )
        return letters

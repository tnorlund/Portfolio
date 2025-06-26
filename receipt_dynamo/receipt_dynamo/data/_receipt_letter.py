from botocore.exceptions import ClientError
from receipt_dynamo import ReceiptLetter, itemToReceiptLetter
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.util import assert_valid_uuid


class _ReceiptLetter(DynamoClientProtocol):
    """
    A class used to access receipt letters in DynamoDB.

    Methods
    -------
    addReceiptLetter(letter: ReceiptLetter)
        Adds a ReceiptLetter to DynamoDB.
    addReceiptLetters(letters: list[ReceiptLetter])
        Adds multiple ReceiptLetters to DynamoDB in batches.
    updateReceiptLetter(letter: ReceiptLetter)
        Updates an existing ReceiptLetter in the database.
    updateReceiptLetters(letters: list[ReceiptLetter])
        Updates multiple ReceiptLetters in the database.
    deleteReceiptLetter(letter: ReceiptLetter)
        Deletes a single ReceiptLetter by IDs.
    deleteReceiptLetters(letters: list[ReceiptLetter])
        Deletes multiple ReceiptLetters in batch.
    getReceiptLetter(
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int,
        letter_id: int
    ) -> ReceiptLetter:
        Retrieves a single ReceiptLetter by IDs.
    listReceiptLetters(
        limit: int = None,
        lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptLetter], dict | None]:
        Returns ReceiptLetters and the last evaluated key.
    listReceiptLettersFromWord(
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int
    ) -> list[ReceiptLetter]:
        Returns all ReceiptLetters for a given word.
    """

    def addReceiptLetter(self, letter: ReceiptLetter):
        """Adds a ReceiptLetter to DynamoDB.

        Args:
            letter (ReceiptLetter): The ReceiptLetter to add.

        Raises:
            ValueError: If the letter is None or not an instance of ReceiptLetter.
            Exception: If the letter cannot be added to DynamoDB.
        """
        if letter is None:
            raise ValueError("letter parameter is required and cannot be None.")
        if not isinstance(letter, ReceiptLetter):
            raise ValueError("letter must be an instance of the ReceiptLetter class.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add receipt letter to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(f"Could not add receipt letter to DynamoDB: {e}") from e

    def addReceiptLetters(self, letters: list[ReceiptLetter]):
        """Adds multiple ReceiptLetters to DynamoDB in batches.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to add.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be added to DynamoDB.
        """
        if letters is None:
            raise ValueError("letters parameter is required and cannot be None.")
        if not isinstance(letters, list):
            raise ValueError("letters must be a list of ReceiptLetter instances.")
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        try:
            for i in range(0, len(letters), 25):
                chunk = letters[i : i + 25]
                request_items = [{"PutRequest": {"Item": lt.to_item()}} for lt in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not add ReceiptLetters to the database: {e}"
                ) from e

    def updateReceiptLetter(self, letter: ReceiptLetter):
        """Updates an existing ReceiptLetter in the database.

        Args:
            letter (ReceiptLetter): The ReceiptLetter to update.

        Raises:
            ValueError: If the letter is None or not an instance of ReceiptLetter.
            Exception: If the letter cannot be updated in DynamoDB.
        """
        if letter is None:
            raise ValueError("letter parameter is required and cannot be None.")
        if not isinstance(letter, ReceiptLetter):
            raise ValueError("letter must be an instance of the ReceiptLetter class.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=letter.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not update ReceiptLetter in the database: {e}"
                ) from e

    def updateReceiptLetters(self, letters: list[ReceiptLetter]):
        """Updates multiple ReceiptLetters in the database.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to update.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be updated in DynamoDB.
        """
        if letters is None:
            raise ValueError("letters parameter is required and cannot be None.")
        if not isinstance(letters, list):
            raise ValueError("letters must be a list of ReceiptLetter instances.")
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        for i in range(0, len(letters), 25):
            chunk = letters[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": lt.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for lt in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "TransactionCanceledException":
                    # Check if cancellation was due to conditional check failure
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more ReceiptLetters do not exist"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception(f"Provisioned throughput exceeded: {e}") from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise Exception(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise Exception(f"Access denied: {e}") from e
                else:
                    raise Exception(
                        f"Could not update ReceiptLetters in the database: {e}"
                    ) from e

    def deleteReceiptLetter(
        self,
        letter: ReceiptLetter,
    ):
        """Deletes a single ReceiptLetter by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            line_id (int): The line ID.
            word_id (int): The word ID.
            letter_id (int): The letter ID.

        Raises:
            ValueError: If the letter ID is None.
            Exception: If the letter cannot be deleted from DynamoDB.
        """
        if letter is None:
            raise ValueError("letter parameter is required and cannot be None.")
        if not isinstance(letter, ReceiptLetter):
            raise ValueError("letter must be an instance of the ReceiptLetter class.")
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=letter.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptLetter with ID {letter.letter_id} does not exist"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptLetter from the database: {e}"
                ) from e

    def deleteReceiptLetters(self, letters: list[ReceiptLetter]):
        """Deletes multiple ReceiptLetters in batch.

        Args:
            letters (list[ReceiptLetter]): The ReceiptLetters to delete.

        Raises:
            ValueError: If the letters are None or not a list.
            Exception: If the letters cannot be deleted from DynamoDB.
        """
        if letters is None:
            raise ValueError("letters parameter is required and cannot be None.")
        if not isinstance(letters, list):
            raise ValueError("letters must be a list of ReceiptLetter instances.")
        if not all(isinstance(lt, ReceiptLetter) for lt in letters):
            raise ValueError(
                "All letters must be instances of the ReceiptLetter class."
            )
        try:
            for i in range(0, len(letters), 25):
                chunk = letters[i : i + 25]
                request_items = [{"DeleteRequest": {"Key": lt.key()}} for lt in chunk]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(RequestItems=unprocessed)
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not delete ReceiptLetters from the database: {e}"
                ) from e

    def getReceiptLetter(
        self,
        receipt_id: int,
        image_id: str,
        line_id: int,
        word_id: int,
        letter_id: int,
    ) -> ReceiptLetter:
        """Retrieves a single ReceiptLetter by IDs.

        Args:
            receipt_id (int): The receipt ID.
            image_id (str): The image ID.
            line_id (int): The line ID.
            word_id (int): The word ID.
            letter_id (int): The letter ID.

        Raises:
            ValueError: If the receipt ID is None or not an integer.
            ValueError: If the image ID is None or not a valid UUID.
            ValueError: If the line ID is None or not an integer.
            ValueError: If the word ID is None or not an integer.
            ValueError: If the letter ID is None or not an integer.
            Exception: If the receipt letter cannot be retrieved from DynamoDB.
        """
        if receipt_id is None:
            raise ValueError("receipt_id parameter is required and cannot be None.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id parameter is required and cannot be None.")
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError("line_id parameter is required and cannot be None.")
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError("word_id parameter is required and cannot be None.")
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")
        if letter_id is None:
            raise ValueError("letter_id parameter is required and cannot be None.")
        if not isinstance(letter_id, int):
            raise ValueError("letter_id must be an integer.")
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#"
                        f"LINE#{line_id:05d}#"
                        f"WORD#{word_id:05d}#"
                        f"LETTER#{letter_id:05d}"
                    },
                },
            )
            if "Item" in response:
                return itemToReceiptLetter(response["Item"])
            else:
                raise ValueError(f"ReceiptLetter with ID {letter_id} not found")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(f"Error getting receipt letter: {e}") from e

    def listReceiptLetters(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[ReceiptLetter], dict | None]:
        """Returns all ReceiptLetters from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(lastEvaluatedKey, dict):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        receipt_letters = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_LETTER"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_letters.extend(
                [itemToReceiptLetter(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt letters.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    receipt_letters.extend(
                        [itemToReceiptLetter(item) for item in response["Items"]]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_letters, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt letters from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error listing receipt letters: {e}") from e

    def listReceiptLettersFromWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> list[ReceiptLetter]:
        """Returns all ReceiptLetters for a given word."""
        if receipt_id is None:
            raise ValueError("receipt_id parameter is required and cannot be None.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")
        if image_id is None:
            raise ValueError("image_id parameter is required and cannot be None.")
        assert_valid_uuid(image_id)
        if line_id is None:
            raise ValueError("line_id parameter is required and cannot be None.")
        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer.")
        if word_id is None:
            raise ValueError("word_id parameter is required and cannot be None.")
        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer.")

        receipt_letters = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                ExpressionAttributeValues={
                    ":pkVal": {"S": f"IMAGE#{image_id}"},
                    ":skPrefix": {
                        "S": (
                            f"RECEIPT#{receipt_id:05d}"
                            f"#LINE#{line_id:05d}"
                            f"#WORD#{word_id:05d}"
                            f"#LETTER#"
                        )
                    },
                },
            )
            receipt_letters.extend(
                [itemToReceiptLetter(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
                    ExpressionAttributeValues={
                        ":pkVal": {"S": f"IMAGE#{image_id}"},
                        ":skPrefix": {
                            "S": (
                                f"RECEIPT#{receipt_id:05d}"
                                f"#LINE#{line_id:05d}"
                                f"#WORD#{word_id:05d}"
                                f"#LETTER#"
                            )
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_letters.extend(
                    [itemToReceiptLetter(item) for item in response["Items"]]
                )
            return receipt_letters

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Could not list ReceiptLetters from the database: {e}"
                ) from e

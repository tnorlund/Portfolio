from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptWord, itemToReceiptWord
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.util import assert_valid_uuid

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptWord:
    """
    A class used to represent a ReceiptWord in the database.

    Methods
    -------
    addReceiptWord(word: ReceiptWord)
        Adds a single ReceiptWord.
    addReceiptWords(words: list[ReceiptWord])
        Adds multiple ReceiptWords.
    updateReceiptWord(word: ReceiptWord)
        Updates a ReceiptWord.
    deleteReceiptWord(receipt_id: int, image_id: str, line_id: int, word_id: int)
        Deletes a single ReceiptWord by IDs.
    deleteReceiptWords(words: list[ReceiptWord])
        Deletes multiple ReceiptWords.
    deleteReceiptWordsFromLine(receipt_id: int, image_id: str, line_id: int)
        Deletes all ReceiptWords from a given line within a receipt/image.
    getReceiptWord(receipt_id: int, image_id: str, line_id: int, word_id: int) -> ReceiptWord
        Retrieves a single ReceiptWord by IDs.
    listReceiptWords() -> list[ReceiptWord]
        Returns all ReceiptWords from the table.
    listReceiptWordsFromLine(receipt_id: int, image_id: str, line_id: int) -> list[ReceiptWord]
        Returns all ReceiptWords that match the given receipt/image/line IDs.
    listReceiptWordsFromReceipt(image_id: str, receipt_id: int) -> list[ReceiptWord]
        Returns all ReceiptWords that match the given receipt/image IDs.
    """

    def addReceiptWord(self, word: ReceiptWord):
        """Adds a single ReceiptWord to DynamoDB."""
        if word is None:
            raise ValueError("word parameter is required and cannot be None.")
        if not isinstance(word, ReceiptWord):
            raise ValueError(
                "word must be an instance of the ReceiptWord class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"ReceiptWord with ID {word.word_id} already exists"
                )
            elif error_code == "ResourceNotFoundException":
                raise Exception("Could not add ReceiptWords to DynamoDB: ")
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception("Provisioned throughput exceeded")
            elif error_code == "InternalServerError":
                raise Exception("Internal server error")
            elif error_code == "ValidationException":
                raise Exception("One or more parameters given were invalid")
            elif error_code == "AccessDeniedException":
                raise Exception("Access denied")
            else:
                raise Exception("Could not add ReceiptWords to DynamoDB: ")

    def addReceiptWords(self, words: list[ReceiptWord]):
        """Adds multiple ReceiptWords to DynamoDB in batches of CHUNK_SIZE."""
        if words is None:
            raise ValueError("words parameter is required and cannot be None.")
        if not isinstance(words, list):
            raise ValueError("words must be a list of ReceiptWord instances.")
        if not all(isinstance(w, ReceiptWord) for w in words):
            raise ValueError(
                "All words must be instances of the ReceiptWord class."
            )
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    {"PutRequest": {"Item": w.to_item()}} for w in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("already exists")
            elif error_code == "ResourceNotFoundException":
                raise Exception("Could not add receipt word to DynamoDB")
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception("Provisioned throughput exceeded")
            elif error_code == "InternalServerError":
                raise Exception("Internal server error")
            elif error_code == "ValidationException":
                raise Exception("One or more parameters given were invalid")
            elif error_code == "AccessDeniedException":
                raise Exception("Access denied")
            else:
                raise Exception("Could not add receipt word to DynamoDB")

    def updateReceiptWord(self, word: ReceiptWord):
        """Updates an existing ReceiptWord in DynamoDB."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=word.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"ReceiptWord with ID {word.word_id} does not exist"
                )
            else:
                raise Exception(
                    f"Could not update ReceiptWord in the database: {e}"
                )

    def updateReceiptWords(self, words: list[ReceiptWord]):
        """Updates multiple existing ReceiptWords in DynamoDB."""
        if words is None:
            raise ValueError("words parameter is required and cannot be None.")
        if not isinstance(words, list):
            raise ValueError("words must be a list of ReceiptWord instances.")
        if not all(isinstance(w, ReceiptWord) for w in words):
            raise ValueError(
                "All words must be instances of the ReceiptWord class."
            )
        for i in range(0, len(words), 25):
            chunk = words[i : i + 25]
            transact_items = [
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": w.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
                for w in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError("One or more ReceiptWords do not exist")
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception("Provisioned throughput exceeded")
                elif error_code == "InternalServerError":
                    raise Exception("Internal server error")
                elif error_code == "ValidationException":
                    raise Exception(
                        "One or more parameters given were invalid"
                    )
                elif error_code == "AccessDeniedException":
                    raise Exception("Access denied")
                else:
                    raise ValueError(
                        f"Could not update ReceiptWords in the database: {e}"
                    )

    def deleteReceiptWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ):
        """Deletes a single ReceiptWord by IDs."""
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(f"ReceiptWord with ID {word_id} not found")
            else:
                raise

    def deleteReceiptWords(self, words: list[ReceiptWord]):
        """Deletes multiple ReceiptWords in batch."""
        try:
            for i in range(0, len(words), CHUNK_SIZE):
                chunk = words[i : i + CHUNK_SIZE]
                request_items = [
                    {"DeleteRequest": {"Key": w.key()}} for w in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise ValueError(
                f"Could not delete ReceiptWords from the database: {e}"
            )

    def deleteReceiptWordsFromLine(
        self, receipt_id: int, image_id: str, line_id: int
    ):
        """Deletes all ReceiptWords from a given line within a receipt/image."""
        words = self.listReceiptWordsFromLine(receipt_id, image_id, line_id)
        self.deleteReceiptWords(words)

    def getReceiptWord(
        self, receipt_id: int, image_id: str, line_id: int, word_id: int
    ) -> ReceiptWord:
        """Retrieves a single ReceiptWord by IDs."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"
                    },
                },
            )
            return itemToReceiptWord(response["Item"])
        except KeyError:
            raise ValueError(f"ReceiptWord with ID {word_id} not found")

    def getReceiptWordsByIndices(
        self, indices: list[tuple[str, int, int, int]]
    ) -> list[ReceiptWord]:
        """Retrieves multiple ReceiptWords by their indices."""
        if indices is None:
            raise ValueError(
                "indices parameter is required and cannot be None."
            )
        if not isinstance(indices, list):
            raise ValueError("indices must be a list of tuples.")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("indices must be a list of tuples.")
        for index in indices:
            if len(index) != 4:
                raise ValueError(
                    "indices must be a list of tuples with 4 elements."
                )
            if not isinstance(index[0], str):
                raise ValueError("First element of tuple must be a string.")
            assert_valid_uuid(index[0])
            if not isinstance(index[1], int):
                raise ValueError("Second element of tuple must be an integer.")
            if index[1] <= 0:
                raise ValueError("Second element of tuple must be positive.")
            if not isinstance(index[2], int):
                raise ValueError("Third element of tuple must be an integer.")
            if index[2] <= 0:
                raise ValueError("Third element of tuple must be positive.")
            if not isinstance(index[3], int):
                raise ValueError("Fourth element of tuple must be an integer.")
            if index[3] <= 0:
                raise ValueError("Fourth element of tuple must be positive.")

        keys = [
            {
                "PK": {"S": f"IMAGE#{index[0]}"},
                "SK": {
                    "S": f"RECEIPT#{index[1]:05d}#LINE#{index[2]:05d}#WORD#{index[3]:05d}"
                },
            }
            for index in indices
        ]
        return self.getReceiptWordsByKeys(keys)

    def getReceiptWordsByKeys(self, keys: list[dict]) -> list[ReceiptWord]:
        # Check the validity of the keys
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("Keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise ValueError("SK must start with 'RECEIPT#'")
            if not key["SK"]["S"].split("#")[2] == "LINE":
                raise ValueError("SK must contain 'LINE'")
            if not key["SK"]["S"].split("#")[4] == "WORD":
                raise ValueError("SK must contain 'WORD'")
        results = []

        try:
            # Split keys into chunks of up to 100
            for i in range(0, len(keys), CHUNK_SIZE):
                chunk = keys[i : i + CHUNK_SIZE]

                # Prepare parameters for BatchGetItem
                request = {
                    "RequestItems": {
                        self.table_name: {
                            "Keys": chunk,
                        }
                    }
                }

                # Perform BatchGet
                response = self._client.batch_get_item(**request)

                # Combine all found items
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)

                # Retry unprocessed keys if any
                unprocessed = response.get("UnprocessedKeys", {})
                while unprocessed.get(self.table_name, {}).get("Keys"):
                    response = self._client.batch_get_item(
                        RequestItems=unprocessed
                    )
                    batch_items = response["Responses"].get(
                        self.table_name, []
                    )
                    results.extend(batch_items)
                    unprocessed = response.get("UnprocessedKeys", {})

            return [itemToReceiptWord(result) for result in results]

        except ClientError as e:
            raise ValueError(
                f"Could not get ReceiptWords from the database: {e}"
            )

    def listReceiptWords(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords from the table."""
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if lastEvaluatedKey is not None and not isinstance(
            lastEvaluatedKey, dict
        ):
            raise ValueError("lastEvaluatedKey must be a dictionary or None.")

        receipt_words = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_WORD"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            receipt_words.extend(
                [itemToReceiptWord(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the receipt words.
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    receipt_words.extend(
                        [itemToReceiptWord(item) for item in response["Items"]]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return receipt_words, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt words from DynamoDB: {e}"
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
                raise Exception(f"Error listing receipt words: {e}") from e

    def listReceiptWordsFromLine(
        self, receipt_id: int, image_id: str, line_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image/line IDs."""
        receipt_words = []
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                ExpressionAttributeValues={
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_val": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                    },
                },
            )
            receipt_words.extend(
                [itemToReceiptWord(item) for item in response["Items"]]
            )

            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="#pk = :pk_val AND begins_with(#sk, :sk_val)",
                    ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
                    ExpressionAttributeValues={
                        ":pk_val": {"S": f"IMAGE#{image_id}"},
                        ":sk_val": {
                            "S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
                        },
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                receipt_words.extend(
                    [itemToReceiptWord(item) for item in response["Items"]]
                )
            return receipt_words
        except ClientError as e:
            raise ValueError(
                f"Could not list ReceiptWords from the database: {e}"
            )

    def listReceiptWordsFromReceipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given receipt/image IDs.

        Args:
            image_id (str): The ID of the image
            receipt_id (int): The ID of the receipt

        Returns:
            list[ReceiptWord]: List of ReceiptWord entities for the given receipt

        Raises:
            ValueError: If the parameters are invalid or if there's an error querying DynamoDB
        """
        if image_id is None:
            raise ValueError(
                "image_id parameter is required and cannot be None."
            )
        if receipt_id is None:
            raise ValueError(
                "receipt_id parameter is required and cannot be None."
            )
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string.")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer.")

        receipt_words = []
        try:
            # Query parameters using BETWEEN to get only WORD items
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk_val AND #sk BETWEEN :sk_start AND :sk_end",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
                "ExpressionAttributeValues": {
                    ":pk_val": {"S": f"IMAGE#{image_id}"},
                    ":sk_start": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
                    ":sk_end": {
                        "S": f"RECEIPT#{receipt_id:05d}#LINE#\uffff#WORD#\uffff"
                    },
                },
            }

            # Initial query
            response = self._client.query(**query_params)
            receipt_words.extend(
                [
                    itemToReceiptWord(item)
                    for item in response["Items"]
                    if "#WORD#" in item["SK"]["S"]
                    and not item["SK"]["S"].endswith("#TAG#")
                    and not item["SK"]["S"].endswith("#LETTER#")
                ]
            )

            # Handle pagination
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                receipt_words.extend(
                    [
                        itemToReceiptWord(item)
                        for item in response["Items"]
                        if "#WORD#" in item["SK"]["S"]
                        and not item["SK"]["S"].endswith("#TAG#")
                        and not item["SK"]["S"].endswith("#LETTER#")
                    ]
                )

            return receipt_words

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list receipt words from DynamoDB: {e}"
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
                raise Exception(f"Error listing receipt words: {e}") from e

    def listReceiptWordsByEmbeddingStatus(
        self, embedding_status: EmbeddingStatus
    ) -> list[ReceiptWord]:
        """Returns all ReceiptWords that match the given embedding status."""
        receipt_words: list[ReceiptWord] = []
        # Validate and normalize embedding_status argument
        if isinstance(embedding_status, EmbeddingStatus):
            status_str = embedding_status.value
        elif isinstance(embedding_status, str):
            status_str = embedding_status
        else:
            raise ValueError(
                "embedding_status must be a string or EmbeddingStatus enum"
            )
        # Ensure the status_str is a valid EmbeddingStatus value
        valid_values = [s.value for s in EmbeddingStatus]
        if status_str not in valid_values:
            raise ValueError(
                f"embedding_status must be one of: {', '.join(valid_values)}; Got: {status_str}"
            )
        try:
            # Query the GSI1 index on embedding status
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression="#gsi1pk = :status",
                ExpressionAttributeNames={"#gsi1pk": "GSI1PK"},
                ExpressionAttributeValues={
                    ":status": {"S": f"EMBEDDING_STATUS#{status_str}"}
                },
            )
            # First page
            for item in response.get("Items", []):
                receipt_words.append(itemToReceiptWord(item))
            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    IndexName="GSI1",
                    KeyConditionExpression="#gsi1pk = :status",
                    ExpressionAttributeNames={"#gsi1pk": "GSI1PK"},
                    ExpressionAttributeValues={
                        ":status": {"S": f"EMBEDDING_STATUS#{status_str}"}
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                for item in response.get("Items", []):
                    receipt_words.append(itemToReceiptWord(item))
            return receipt_words
        except ClientError as e:
            raise ValueError(
                f"Could not list receipt words by embedding status: {e}"
            ) from e

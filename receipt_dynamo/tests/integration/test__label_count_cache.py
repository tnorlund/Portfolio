"""
Integration tests for LabelCountCache operations in DynamoDB.

This module tests the LabelCountCache-related methods of DynamoClient,
# including
add, get, update, and list operations. It follows the perfect
# test patterns
established in test__receipt.py, test__image.py, test__word.py, and
# test__letter.py.
"""

import time
from datetime import datetime
from typing import List, Literal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.label_count_cache import LabelCountCache

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def example_label_count_cache() -> LabelCountCache:
    """Create a sample LabelCountCache for testing."""
    return LabelCountCache(
        label="TEST_LABEL",
        valid_count=10,
        invalid_count=2,
        pending_count=5,
        needs_review_count=3,
        none_count=1,
        last_updated=datetime.now().isoformat(),
    )


@pytest.fixture
def example_label_count_cache_with_ttl() -> LabelCountCache:
    """Create a sample LabelCountCache with TTL for testing."""
    future_ttl = int(time.time()) + 3600  # 1 hour from now
    return LabelCountCache(
        label="TTL_LABEL",
        valid_count=5,
        invalid_count=1,
        pending_count=2,
        needs_review_count=1,
        none_count=0,
        last_updated=datetime.now().isoformat(),
        time_to_live=future_ttl,
    )


@pytest.fixture
def example_label_count_caches() -> List[LabelCountCache]:
    """Create a list of LabelCountCaches for batch testing."""
    now = datetime.now().isoformat()
    return [
        LabelCountCache(
            label="DATE",
            valid_count=100,
            invalid_count=5,
            pending_count=10,
            needs_review_count=2,
            none_count=1,
            last_updated=now,
        ),
        LabelCountCache(
            label="AMOUNT",
            valid_count=150,
            invalid_count=3,
            pending_count=8,
            needs_review_count=5,
            none_count=2,
            last_updated=now,
        ),
        LabelCountCache(
            label="MERCHANT",
            valid_count=200,
            invalid_count=10,
            pending_count=15,
            needs_review_count=7,
            none_count=3,
            last_updated=now,
        ),
    ]


class TestLabelCountCacheBasicOperations:
    """Test basic CRUD operations for LabelCountCache."""

    def test_add_label_count_cache_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test successful addition of a label count cache."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_cache(example_label_count_cache)
        result = client.get_label_count_cache(example_label_count_cache.label)
        assert result == example_label_count_cache

    def test_add_label_count_cache_with_ttl_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache_with_ttl: LabelCountCache,
    ) -> None:
        """Test successful addition of a label count cache with TTL."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_cache(example_label_count_cache_with_ttl)
        result = client.get_label_count_cache(example_label_count_cache_with_ttl.label)
        assert result == example_label_count_cache_with_ttl

    def test_get_label_count_cache_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test successful retrieval of a label count cache."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_cache(example_label_count_cache)
        result = client.get_label_count_cache(example_label_count_cache.label)
        assert result == example_label_count_cache

    def test_get_label_count_cache_not_found_returns_none(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that getting a non-existent label count cache returns None."""
        client = DynamoClient(dynamodb_table)
        result = client.get_label_count_cache("NON_EXISTENT")
        assert result is None

    def test_update_label_count_cache_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test successful update of a label count cache."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_cache(example_label_count_cache)
        example_label_count_cache.valid_count = 50
        example_label_count_cache.invalid_count = 10
        client.update_label_count_cache(example_label_count_cache)
        result = client.get_label_count_cache(example_label_count_cache.label)
        assert result == example_label_count_cache
        assert result.valid_count == 50
        assert result.invalid_count == 10

    def test_update_label_count_cache_not_found_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test that updating a non-existent label count cache raises error."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityNotFoundError, match="not found"):
            client.update_label_count_cache(example_label_count_cache)

    def test_add_duplicate_label_count_cache_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test that adding a duplicate label count cache raises error."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_cache(example_label_count_cache)
        duplicate = LabelCountCache(
            label=example_label_count_cache.label,
            valid_count=20,
            invalid_count=5,
            pending_count=3,
            needs_review_count=1,
            none_count=0,
            last_updated=datetime.now().isoformat(),
        )
        with pytest.raises(EntityValidationError, match="already exists"):
            client.add_label_count_cache(duplicate)


class TestLabelCountCacheBatchOperations:
    """Test batch operations for LabelCountCache."""

    def test_add_label_count_caches_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
    ) -> None:
        """Test successful batch addition of label count caches."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_caches(example_label_count_caches)

        for cache in example_label_count_caches:
            result = client.get_label_count_cache(cache.label)
            assert result == cache

    def test_add_large_batch_label_count_caches_success(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test successful batch addition of 100 label count caches."""
        client = DynamoClient(dynamodb_table)
        now = datetime.now().isoformat()
        large_batch = [
            LabelCountCache(
                label=f"LABEL_{i:03d}",
                valid_count=i,
                invalid_count=i % 5,
                pending_count=i % 3,
                needs_review_count=i % 7,
                none_count=i % 2,
                last_updated=now,
            )
            for i in range(100)
        ]

        client.add_label_count_caches(large_batch)

        # Verify a sample of the added caches
        for i in [0, 25, 50, 75, 99]:
            result = client.get_label_count_cache(f"LABEL_{i:03d}")
            assert result == large_batch[i]

    def test_add_empty_list_label_count_caches_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding an empty list raises OperationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(OperationError, match="Parameter validation failed"):
            client.add_label_count_caches([])


class TestLabelCountCacheListOperations:
    """Test list and query operations for LabelCountCache."""

    def test_list_label_count_caches_success(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
    ) -> None:
        """Test successful listing of label count caches."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_caches(example_label_count_caches)

        results, last_key = client.list_label_count_caches()

        assert len(results) == len(example_label_count_caches)
        # Sort both lists by label for comparison
        results.sort(key=lambda x: x.label)
        example_label_count_caches.sort(key=lambda x: x.label)
        assert results == example_label_count_caches
        assert last_key is None

    def test_list_label_count_caches_with_limit(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
    ) -> None:
        """Test listing label count caches with limit."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_caches(example_label_count_caches)

        results, last_key = client.list_label_count_caches(limit=2)

        assert len(results) == 2
        assert last_key is not None

    def test_list_label_count_caches_pagination(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
    ) -> None:
        """Test pagination through label count caches."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_caches(example_label_count_caches)

        # Get first page
        first_results, first_key = client.list_label_count_caches(limit=2)
        assert len(first_results) == 2
        assert first_key is not None

        # Get second page
        second_results, second_key = client.list_label_count_caches(
            limit=2, last_evaluated_key=first_key
        )
        assert len(second_results) == 1
        assert second_key is None

        # Verify no overlap
        first_labels = {cache.label for cache in first_results}
        second_labels = {cache.label for cache in second_results}
        assert first_labels.isdisjoint(second_labels)

    def test_list_empty_label_count_caches(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test listing when no label count caches exist."""
        client = DynamoClient(dynamodb_table)
        results, last_key = client.list_label_count_caches()
        assert results == []
        assert last_key is None

    def test_list_label_count_caches_with_zero_limit(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
    ) -> None:
        """Test listing label count caches with zero limit."""
        client = DynamoClient(dynamodb_table)
        client.add_label_count_caches(example_label_count_caches)

        results, last_key = client.list_label_count_caches(limit=0)
        assert results == []
        assert last_key is None


class TestLabelCountCacheValidation:
    """Test input validation for LabelCountCache operations."""

    def test_add_label_count_cache_none_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding None raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError, match="item cannot be None"):
            client.add_label_count_cache(None)  # type: ignore

    def test_add_label_count_cache_wrong_type_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding wrong type raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(
            EntityValidationError,
            match="item must be an instance of the LabelCountCache class",
        ):
            client.add_label_count_cache("not-a-cache")  # type: ignore

    def test_add_label_count_caches_none_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding None list raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError, match="items cannot be None"):
            client.add_label_count_caches(None)  # type: ignore

    def test_add_label_count_caches_not_list_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that adding non-list raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(
            EntityValidationError,
            match="items must be a list of LabelCountCache objects.f",
        ):
            client.add_label_count_caches("not-a-list")  # type: ignore

    def test_add_label_count_caches_wrong_item_type_raises_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
    ) -> None:
        """Test that adding list with wrong item type raises error."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(
            EntityValidationError,
            match="items must be a list of LabelCountCache objects.f",
        ):
            client.add_label_count_caches(
                [example_label_count_cache, "not-a-cache"]  # type: ignore
            )

    def test_update_label_count_cache_none_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that updating None raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(EntityValidationError, match="item cannot be None"):
            client.update_label_count_cache(None)  # type: ignore

    def test_update_label_count_cache_wrong_type_raises_error(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test that updating wrong type raises EntityValidationError."""
        client = DynamoClient(dynamodb_table)
        with pytest.raises(
            EntityValidationError,
            match="item must be an instance of the LabelCountCache class",
        ):
            client.update_label_count_cache("not-a-cache")  # type: ignore


@pytest.mark.parametrize(
    "error_code,expected_exception",
    [
        ("ConditionalCheckFailedException", EntityValidationError),
        ("ValidationException", EntityValidationError),
        ("ResourceNotFoundException", OperationError),
        ("ItemCollectionSizeLimitExceededException", DynamoDBError),
        ("TransactionConflictException", DynamoDBError),
        ("RequestLimitExceeded", DynamoDBError),
        ("ProvisionedThroughputExceededException", DynamoDBError),
        ("InternalServerError", DynamoDBError),
        ("ServiceUnavailable", DynamoDBError),
        ("UnknownError", DynamoDBError),
    ],
)
class TestLabelCountCacheErrorHandling:
    """Test error handling for LabelCountCache operations."""

    def test_add_label_count_cache_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test that DynamoDB errors are properly handled in add operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "put_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem",
            ),
        ):
            with pytest.raises(expected_exception):
                client.add_label_count_cache(example_label_count_cache)

    def test_add_label_count_caches_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_caches: List[LabelCountCache],
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test DynamoDB errors in batch add operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "batch_write_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "BatchWriteItem",
            ),
        ):
            with pytest.raises(expected_exception):
                client.add_label_count_caches(example_label_count_caches)

    def test_get_label_count_cache_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test that DynamoDB errors are properly handled in get operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "get_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "GetItem",
            ),
        ):
            with pytest.raises(expected_exception):
                client.get_label_count_cache("TEST_LABEL")

    def test_update_label_count_cache_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_label_count_cache: LabelCountCache,
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test DynamoDB errors in update operations."""
        client = DynamoClient(dynamodb_table)
        # ConditionalCheckFailedException for update means entity not found
        if error_code == "ConditionalCheckFailedException":
            expected_exception = EntityNotFoundError
        with patch.object(
            client._client,
            "put_item",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem",
            ),
        ):
            with pytest.raises(expected_exception):
                client.update_label_count_cache(example_label_count_cache)

    def test_list_label_count_caches_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test DynamoDB errors in list operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client,
            "query",
            side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "Query",
            ),
        ):
            with pytest.raises(expected_exception):
                client.list_label_count_caches()


class TestLabelCountCacheSpecialCases:
    """Test special cases and edge conditions for LabelCountCache."""

    def test_label_count_cache_with_unicode_label(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test label count cache with unicode characters in label."""
        client = DynamoClient(dynamodb_table)
        unicode_cache = LabelCountCache(
            label="测试标签",
            valid_count=5,
            invalid_count=2,
            pending_count=1,
            needs_review_count=0,
            none_count=1,
            last_updated=datetime.now().isoformat(),
        )

        client.add_label_count_cache(unicode_cache)
        result = client.get_label_count_cache(unicode_cache.label)
        assert result == unicode_cache

    def test_label_count_cache_with_special_characters(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test label count cache with special characters in label."""
        client = DynamoClient(dynamodb_table)
        special_cache = LabelCountCache(
            label="LABEL_WITH_@#$%_CHARS",
            valid_count=10,
            invalid_count=3,
            pending_count=2,
            needs_review_count=1,
            none_count=0,
            last_updated=datetime.now().isoformat(),
        )

        client.add_label_count_cache(special_cache)
        result = client.get_label_count_cache(special_cache.label)
        assert result == special_cache

    def test_label_count_cache_with_zero_counts(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test label count cache with all zero counts."""
        client = DynamoClient(dynamodb_table)
        zero_cache = LabelCountCache(
            label="ZERO_COUNTS",
            valid_count=0,
            invalid_count=0,
            pending_count=0,
            needs_review_count=0,
            none_count=0,
            last_updated=datetime.now().isoformat(),
        )

        client.add_label_count_cache(zero_cache)
        result = client.get_label_count_cache(zero_cache.label)
        assert result == zero_cache

    def test_label_count_cache_with_max_counts(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test label count cache with maximum integer counts."""
        client = DynamoClient(dynamodb_table)
        max_cache = LabelCountCache(
            label="MAX_COUNTS",
            valid_count=999999,
            invalid_count=888888,
            pending_count=777777,
            needs_review_count=666666,
            none_count=555555,
            last_updated=datetime.now().isoformat(),
        )

        client.add_label_count_cache(max_cache)
        result = client.get_label_count_cache(max_cache.label)
        assert result == max_cache

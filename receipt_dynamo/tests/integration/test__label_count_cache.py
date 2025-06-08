from datetime import datetime
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.label_count_cache import LabelCountCache


@pytest.fixture
def sample_cache() -> LabelCountCache:
    return LabelCountCache(
        label="DATE",
        valid_count=1,
        invalid_count=0,
        pending_count=0,
        needs_review_count=0,
        none_count=0,
        last_updated=datetime.now().isoformat(),
    )


@pytest.fixture
def sample_caches() -> list[LabelCountCache]:
    return [
        LabelCountCache(
            label="DATE",
            valid_count=1,
            invalid_count=0,
            pending_count=0,
            needs_review_count=0,
            none_count=0,
            last_updated=datetime.now().isoformat(),
        ),
        LabelCountCache(
            label="AMOUNT",
            valid_count=1,
            invalid_count=0,
            pending_count=0,
            needs_review_count=3,
            none_count=0,
            last_updated=datetime.now().isoformat(),
        ),
    ]


@pytest.mark.integration
def test_add_and_get(
    dynamodb_table: Literal["MyMockedTable"], sample_cache: LabelCountCache
) -> None:
    client = DynamoClient(dynamodb_table)
    client.addLabelCountCache(sample_cache)
    fetched = client.getLabelCountCache("DATE")
    assert fetched == sample_cache


@pytest.mark.integration
def test_update(
    dynamodb_table: Literal["MyMockedTable"], sample_cache: LabelCountCache
) -> None:
    client = DynamoClient(dynamodb_table)
    client.addLabelCountCache(sample_cache)
    sample_cache.valid_count = 2
    client.updateLabelCountCache(sample_cache)
    fetched = client.getLabelCountCache("DATE")
    assert fetched == sample_cache


@pytest.mark.integration
def test_add_and_list(
    dynamodb_table: Literal["MyMockedTable"],
    sample_caches: list[LabelCountCache],
) -> None:
    client = DynamoClient(dynamodb_table)
    client.addLabelCountCaches(sample_caches)
    fetched, _ = client.listLabelCountCaches()
    assert len(fetched) == len(sample_caches)
    # sort the fetched caches by label
    fetched.sort(key=lambda x: x.label)
    # sort the sample caches by label
    sample_caches.sort(key=lambda x: x.label)
    assert fetched == sample_caches

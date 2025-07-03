from datetime import datetime

import pytest

from receipt_dynamo.entities.label_count_cache import (
    LabelCountCache, item_to_label_count_cache)


@pytest.fixture
def sample_cache() -> LabelCountCache:
    return LabelCountCache(
        label="DATE",
        valid_count=10,
        invalid_count=2,
        pending_count=3,
        needs_review_count=4,
        none_count=0,
        last_updated=datetime.now().isoformat(),
    )


@pytest.mark.unit
def test_init_valid(sample_cache: LabelCountCache) -> None:
    assert sample_cache.label == "DATE"
    assert sample_cache.valid_count == 10
    assert sample_cache.invalid_count == 2
    assert sample_cache.pending_count == 3
    assert sample_cache.needs_review_count == 4
    assert sample_cache.none_count == 0


@pytest.mark.unit
def test_init_invalid_label() -> None:
    with pytest.raises(ValueError):
        LabelCountCache(
            label="",
            valid_count=0,
            invalid_count=0,
            pending_count=0,
            needs_review_count=0,
            none_count=0,
            last_updated="2020-01-01T00:00:00",
        )


@pytest.mark.unit
def test_init_invalid_valid_count() -> None:
    with pytest.raises(ValueError):
        LabelCountCache(
            label="A",
            valid_count=-1,
            invalid_count=0,
            pending_count=0,
            needs_review_count=0,
            none_count=0,
            last_updated="2020-01-01T00:00:00",
        )


@pytest.mark.unit
def test_init_invalid_last_updated() -> None:
    with pytest.raises(ValueError):
        LabelCountCache(
            label="A",
            valid_count=0,
            invalid_count=0,
            pending_count=0,
            needs_review_count=0,
            none_count=0,
            last_updated="bad-date",
        )


@pytest.mark.unit
def test_to_item_and_back(sample_cache: LabelCountCache) -> None:
    item = sample_cache.to_item()
    restored = item_to_label_count_cache(item)
    assert restored == sample_cache

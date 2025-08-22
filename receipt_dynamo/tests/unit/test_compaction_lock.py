# pylint: disable=redefined-outer-name,unused-variable
"""Unit tests for CompactionLock entity."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.compaction_lock import (
    CompactionLock,
    item_to_compaction_lock,
)


@pytest.fixture
def example_compaction_lock():
    return CompactionLock(
        lock_id="chroma-main-snapshot",
        owner=str(uuid4()),
        expires=datetime(2024, 1, 1, 12, 0, 0),
        collection=ChromaDBCollection.LINES,
        heartbeat=datetime(2024, 1, 1, 11, 30, 0),
    )


@pytest.fixture
def minimal_compaction_lock():
    return CompactionLock(
        lock_id="test-lock",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.WORDS,
    )


# === VALID CONSTRUCTION ===


@pytest.mark.unit
def test_compaction_lock_init_valid_datetime(example_compaction_lock):
    assert example_compaction_lock.lock_id == "chroma-main-snapshot"
    assert example_compaction_lock.collection == ChromaDBCollection.LINES
    assert isinstance(example_compaction_lock.expires, str)  # Should be converted to ISO string
    assert isinstance(example_compaction_lock.heartbeat, str)  # Should be converted to ISO string


@pytest.mark.unit
def test_compaction_lock_init_valid_string_dates(minimal_compaction_lock):
    assert minimal_compaction_lock.lock_id == "test-lock"
    assert minimal_compaction_lock.collection == ChromaDBCollection.WORDS
    assert minimal_compaction_lock.expires == "2024-01-01T12:00:00"
    assert minimal_compaction_lock.heartbeat is None


@pytest.mark.unit
def test_compaction_lock_init_both_collections():
    """Test initialization with both collection types."""
    lines_lock = CompactionLock(
        lock_id="lines-lock",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.LINES,
    )
    words_lock = CompactionLock(
        lock_id="words-lock",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.WORDS,
    )
    
    assert lines_lock.collection == ChromaDBCollection.LINES
    assert words_lock.collection == ChromaDBCollection.WORDS


@pytest.mark.unit
def test_compaction_lock_to_item_and_back(example_compaction_lock):
    item = example_compaction_lock.to_item()
    reconstructed = item_to_compaction_lock(item)
    assert reconstructed == example_compaction_lock


@pytest.mark.unit
def test_compaction_lock_to_item_and_back_minimal(minimal_compaction_lock):
    item = minimal_compaction_lock.to_item()
    reconstructed = item_to_compaction_lock(item)
    assert reconstructed == minimal_compaction_lock


@pytest.mark.unit
def test_compaction_lock_key_format(example_compaction_lock):
    key = example_compaction_lock.key
    assert key["PK"]["S"] == "LOCK#lines#chroma-main-snapshot"
    assert key["SK"]["S"] == "LOCK"


@pytest.mark.unit
def test_compaction_lock_gsi1_key_format(example_compaction_lock):
    gsi1_key = example_compaction_lock.gsi1_key
    assert gsi1_key["GSI1PK"]["S"] == "LOCK#lines"
    assert gsi1_key["GSI1SK"]["S"].startswith("EXPIRES#")


@pytest.mark.unit
def test_compaction_lock_to_item_structure(example_compaction_lock):
    item = example_compaction_lock.to_item()
    
    # Check all required fields are present
    assert "PK" in item
    assert "SK" in item
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert "TYPE" in item
    assert "owner" in item
    assert "expires" in item
    assert "collection" in item
    assert "heartbeat" in item
    
    # Check field values
    assert item["TYPE"]["S"] == "COMPACTION_LOCK"
    assert item["collection"]["S"] == "lines"
    assert item["owner"]["S"] == example_compaction_lock.owner


@pytest.mark.unit
def test_compaction_lock_to_item_null_heartbeat(minimal_compaction_lock):
    item = minimal_compaction_lock.to_item()
    assert item["heartbeat"]["NULL"] is True


# === INVALID CONSTRUCTION ===


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None, ""])
def test_compaction_lock_invalid_lock_id_type(bad_value):
    with pytest.raises((ValueError, TypeError)):
        CompactionLock(
            lock_id=bad_value,
            owner=str(uuid4()),
            expires="2024-01-01T12:00:00",
            collection=ChromaDBCollection.LINES,
        )


@pytest.mark.unit
def test_compaction_lock_invalid_owner_not_uuid():
    with pytest.raises(ValueError, match="must be a valid UUID"):
        CompactionLock(
            lock_id="test-lock",
            owner="not-a-uuid",
            expires="2024-01-01T12:00:00",
            collection=ChromaDBCollection.LINES,
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_compaction_lock_invalid_owner_type(bad_value):
    with pytest.raises((ValueError, TypeError)):
        CompactionLock(
            lock_id="test-lock",
            owner=bad_value,
            expires="2024-01-01T12:00:00",
            collection=ChromaDBCollection.LINES,
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_compaction_lock_invalid_expires_type(bad_value):
    with pytest.raises(ValueError, match="expires must be datetime or ISO-8601 string"):
        CompactionLock(
            lock_id="test-lock",
            owner=str(uuid4()),
            expires=bad_value,
            collection=ChromaDBCollection.LINES,
        )


@pytest.mark.unit
def test_compaction_lock_invalid_collection_value():
    with pytest.raises(ValueError):
        CompactionLock(
            lock_id="test-lock",
            owner=str(uuid4()),
            expires="2024-01-01T12:00:00",
            collection="invalid_collection",  # type: ignore
        )


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", [123, None])
def test_compaction_lock_invalid_collection_type(bad_value):
    with pytest.raises((ValueError, TypeError)):
        CompactionLock(
            lock_id="test-lock",
            owner=str(uuid4()),
            expires="2024-01-01T12:00:00",
            collection=bad_value,  # type: ignore
        )


@pytest.mark.unit
def test_compaction_lock_invalid_heartbeat_type_int():
    with pytest.raises(ValueError, match="heartbeat must be datetime, ISO-8601 string, or None"):
        CompactionLock(
            lock_id="test-lock",
            owner=str(uuid4()),
            expires="2024-01-01T12:00:00",
            collection=ChromaDBCollection.LINES,
            heartbeat=123,  # type: ignore
        )


@pytest.mark.unit  
def test_compaction_lock_invalid_heartbeat_type_invalid_string():
    # String heartbeat is allowed (could be validated at usage time)
    lock = CompactionLock(
        lock_id="test-lock",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.LINES,
        heartbeat="not-a-datetime",  # type: ignore
    )
    assert lock.heartbeat == "not-a-datetime"


# === DATETIME HANDLING ===


@pytest.mark.unit
def test_compaction_lock_datetime_to_iso_conversion():
    dt = datetime(2024, 1, 1, 12, 0, 0)
    lock = CompactionLock(
        lock_id="test-lock",
        owner=str(uuid4()),
        expires=dt,
        collection=ChromaDBCollection.LINES,
        heartbeat=dt,
    )
    
    assert lock.expires == "2024-01-01T12:00:00"
    assert lock.heartbeat == "2024-01-01T12:00:00"


@pytest.mark.unit
def test_compaction_lock_iso_string_preserved():
    iso_string = "2024-01-01T12:00:00"
    lock = CompactionLock(
        lock_id="test-lock",
        owner=str(uuid4()),
        expires=iso_string,
        collection=ChromaDBCollection.LINES,
        heartbeat=iso_string,
    )
    
    assert lock.expires == iso_string
    assert lock.heartbeat == iso_string


# === LOCK ID WITH SPECIAL CHARACTERS ===


@pytest.mark.unit
def test_compaction_lock_lock_id_with_hash():
    """Test that lock_id can contain hash characters."""
    lock_id_with_hash = "chroma#test#lock"
    lock = CompactionLock(
        lock_id=lock_id_with_hash,
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.LINES,
    )
    
    item = lock.to_item()
    reconstructed = item_to_compaction_lock(item)
    assert reconstructed.lock_id == lock_id_with_hash


# === PARSING FAILURE ===


@pytest.mark.unit
def test_compaction_lock_missing_keys():
    with pytest.raises(ValueError, match="missing keys"):
        item_to_compaction_lock({})


@pytest.mark.unit
def test_compaction_lock_invalid_pk_format():
    item = {
        "PK": {"S": "INVALID_FORMAT"},
        "SK": {"S": "LOCK"},
        "owner": {"S": str(uuid4())},
        "expires": {"S": "2024-01-01T12:00:00"},
        "collection": {"S": "lines"},
    }
    with pytest.raises(ValueError, match="Invalid lock PK format"):
        item_to_compaction_lock(item)


@pytest.mark.unit
def test_compaction_lock_invalid_collection_in_item():
    item = {
        "PK": {"S": "LOCK#invalid_collection#test-lock"},
        "SK": {"S": "LOCK"},
        "owner": {"S": str(uuid4())},
        "expires": {"S": "2024-01-01T12:00:00"},
        "collection": {"S": "invalid_collection"},
    }
    with pytest.raises(ValueError, match="Invalid collection in item"):
        item_to_compaction_lock(item)


@pytest.mark.unit
def test_compaction_lock_missing_required_field():
    item = {
        "PK": {"S": "LOCK#lines#test-lock"},
        "SK": {"S": "LOCK"},
        # Missing owner
        "expires": {"S": "2024-01-01T12:00:00"},
        "collection": {"S": "lines"},
    }
    with pytest.raises(ValueError, match="missing keys"):
        item_to_compaction_lock(item)


# === EQUALITY, HASHING, STR ===


@pytest.mark.unit
def test_compaction_lock_eq_and_hash(example_compaction_lock):
    duplicate = item_to_compaction_lock(example_compaction_lock.to_item())
    assert duplicate == example_compaction_lock
    assert hash(duplicate) == hash(example_compaction_lock)
    assert example_compaction_lock != "not-a-lock"


@pytest.mark.unit
def test_compaction_lock_str(example_compaction_lock):
    str_repr = str(example_compaction_lock)
    assert "CompactionLock(" in str_repr
    assert example_compaction_lock.lock_id in str_repr
    assert example_compaction_lock.owner in str_repr
    assert "lines" in str_repr


@pytest.mark.unit
def test_compaction_lock_repr(example_compaction_lock):
    repr_str = repr(example_compaction_lock)
    assert "CompactionLock(" in repr_str
    assert example_compaction_lock.lock_id in repr_str
    assert example_compaction_lock.owner in repr_str
    assert "lines" in repr_str


# === EDGE CASES ===


@pytest.mark.unit
def test_compaction_lock_very_long_lock_id():
    """Test with a very long lock_id."""
    long_lock_id = "a" * 1000
    lock = CompactionLock(
        lock_id=long_lock_id,
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.LINES,
    )
    
    item = lock.to_item()
    reconstructed = item_to_compaction_lock(item)
    assert reconstructed.lock_id == long_lock_id


@pytest.mark.unit
def test_compaction_lock_empty_lock_id():
    """Test that empty lock_id is handled appropriately."""
    # This should be handled at the application level, but test current behavior
    with pytest.raises((ValueError, TypeError)):
        CompactionLock(
            lock_id="",
            owner=str(uuid4()),
            expires="2024-01-01T12:00:00",
            collection=ChromaDBCollection.LINES,
        )


@pytest.mark.unit
def test_compaction_lock_future_expiry():
    """Test with future expiry time."""
    future_time = datetime.now() + timedelta(hours=1)
    lock = CompactionLock(
        lock_id="future-lock",
        owner=str(uuid4()),
        expires=future_time,
        collection=ChromaDBCollection.WORDS,
    )
    
    item = lock.to_item()
    reconstructed = item_to_compaction_lock(item)
    assert reconstructed.expires == future_time.isoformat()


# === COLLECTION-SPECIFIC TESTS ===


@pytest.mark.unit
def test_compaction_lock_lines_collection_key():
    """Test that lines collection generates correct keys."""
    lock = CompactionLock(
        lock_id="lines-test",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.LINES,
    )
    
    key = lock.key
    gsi1_key = lock.gsi1_key
    
    assert key["PK"]["S"] == "LOCK#lines#lines-test"
    assert gsi1_key["GSI1PK"]["S"] == "LOCK#lines"


@pytest.mark.unit
def test_compaction_lock_words_collection_key():
    """Test that words collection generates correct keys."""
    lock = CompactionLock(
        lock_id="words-test",
        owner=str(uuid4()),
        expires="2024-01-01T12:00:00",
        collection=ChromaDBCollection.WORDS,
    )
    
    key = lock.key
    gsi1_key = lock.gsi1_key
    
    assert key["PK"]["S"] == "LOCK#words#words-test"
    assert gsi1_key["GSI1PK"]["S"] == "LOCK#words"


# === ITEM CONVERSION EDGE CASES ===


@pytest.mark.unit
def test_item_to_compaction_lock_minimal_valid():
    """Test parsing minimal valid DynamoDB item."""
    item = {
        "PK": {"S": "LOCK#lines#minimal-lock"},
        "SK": {"S": "LOCK"},
        "owner": {"S": str(uuid4())},
        "expires": {"S": "2024-01-01T12:00:00"},
        "collection": {"S": "lines"},
        # No heartbeat field
    }
    
    lock = item_to_compaction_lock(item)
    assert lock.lock_id == "minimal-lock"
    assert lock.collection == ChromaDBCollection.LINES
    assert lock.heartbeat is None


@pytest.mark.unit
def test_item_to_compaction_lock_with_heartbeat():
    """Test parsing DynamoDB item with heartbeat."""
    item = {
        "PK": {"S": "LOCK#words#heartbeat-lock"},
        "SK": {"S": "LOCK"},
        "owner": {"S": str(uuid4())},
        "expires": {"S": "2024-01-01T12:00:00"},
        "collection": {"S": "words"},
        "heartbeat": {"S": "2024-01-01T11:30:00"},
    }
    
    lock = item_to_compaction_lock(item)
    assert lock.lock_id == "heartbeat-lock"
    assert lock.collection == ChromaDBCollection.WORDS
    assert lock.heartbeat == "2024-01-01T11:30:00"
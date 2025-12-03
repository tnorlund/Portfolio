# Protocol vs TypedDict: When to Use Each

## Quick Summary

- **TypedDict**: For data structures (dictionaries with known keys) - like API responses, config objects
- **Protocol**: For interfaces/behaviors (objects with methods) - like "anything that has a `download_file` method"

## TypedDict (You Already Know This)

**Use for**: Structured data, dictionaries with known keys

```python
from typing import Literal, TypedDict
from typing_extensions import NotRequired, Required

class DownloadResult(TypedDict, total=False):
    status: Required[Literal["downloaded", "failed"]]  # Always present
    snapshot_key: NotRequired[str]  # Usually present, but not in exception cases
    local_path: NotRequired[str]  # Only present on successful download
    file_count: NotRequired[int]  # Only present on successful download
    total_size_bytes: NotRequired[int]  # Only present on successful download
    error: NotRequired[str]  # Only present if status == "failed"

def download_snapshot(...) -> DownloadResult:
    return {
        "status": "downloaded",
        "snapshot_key": "path/to/snapshot",
        "local_path": "/local/path",
        "file_count": 42,
        "total_size_bytes": 1024
    }
```

**Benefits**:
- Type checker knows what keys exist
- Type checker knows value types
- IDE autocomplete works
- Clear distinction between required and optional fields

**Note**: TypedDict is a static typing construct and produces plain dicts at runtime. It does not support `isinstance()` or `issubclass()` checks. For runtime validation, use libraries like `pydantic` or write custom validation functions.

## Protocol (Structural Typing)

**Use for**: "Duck typing" - anything that implements certain methods

### The Problem Protocol Solves

Without Protocol, you'd write:
```python
def download_file(s3_client: Any, bucket: str, key: str) -> None:
    s3_client.download_file(Bucket=bucket, Key=key, Filename="local.txt")
```

Mypy can't check if `s3_client` actually has a `download_file` method!

### With Protocol

```python
from typing import Protocol

class S3ClientProtocol(Protocol):
    """Any object that has S3 client methods we need."""
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...
    def upload_file(self, Filename: str, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def list_objects_v2(self, **kwargs: Any) -> Any: ...

def download_file(s3_client: S3ClientProtocol, bucket: str, key: str) -> None:
    s3_client.download_file(Bucket=bucket, Key=key, Filename="local.txt")
    # ✅ Mypy now checks that download_file exists!
```

### How Protocol Works

Protocol uses **structural typing** (also called "duck typing"):
- "If it walks like a duck and quacks like a duck, it's a duck"
- "If it has a `download_file` method, it's an S3ClientProtocol"

**No inheritance needed!** These all work:

```python
# Real boto3 client
s3 = boto3.client("s3")
download_file(s3, "bucket", "key")  # ✅ Works

# Mock object
class MockS3:
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        print(f"Mock download: {Bucket}/{Key}")

mock = MockS3()
download_file(mock, "bucket", "key")  # ✅ Also works!

# Test double
class TestS3Client:
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        self.downloads.append((Bucket, Key, Filename))
    downloads: list = []

test_client = TestS3Client()
download_file(test_client, "bucket", "key")  # ✅ Also works!
```

## Real Example: S3 Client Typing

### Option 1: Use boto3-stubs (Best for Real Clients)

**You already have this dependency!** `boto3-stubs[s3]>=1.34.0` is in your dev dependencies.

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any  # Runtime doesn't need the import

def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    s3_client: S3Client | None = None,  # ✅ Properly typed!
) -> Dict[str, Any]:
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")  # Type checker knows this is S3Client

    s3_client.download_file(...)  # ✅ Full autocomplete and type checking!
```

**Benefits**:
- Full type information from boto3-stubs
- IDE knows all methods and parameters
- Type checker validates method calls
- Zero runtime cost (TYPE_CHECKING is False at runtime)

### Option 2: Use Protocol (Best for Flexibility)

Use Protocol when:
- You want to accept mocks/test doubles
- You only use a few methods
- You want to be explicit about what methods you need

```python
from typing import Protocol, Any

class S3ClientProtocol(Protocol):
    """Protocol for S3 operations we need."""
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...
    def upload_file(self, Filename: str, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def list_objects_v2(self, **kwargs: Any) -> Any: ...
    def get_paginator(self, operation_name: str) -> Any: ...
    def delete_objects(self, Bucket: str, Delete: dict) -> Any: ...
    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> Any: ...

def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    s3_client: S3ClientProtocol | None = None,  # ✅ Accepts any object with these methods
) -> Dict[str, Any]:
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")  # Real client works

    s3_client.download_file(...)  # ✅ Type checked!
```

**Benefits**:
- Works with real boto3 clients
- Works with mocks in tests
- Explicit about what methods you need
- No external dependencies for types

**Trade-off**:
- Less complete than boto3-stubs (only methods you define)
- Need to manually add methods as you use them

## Recommendation for Your Codebase

### For S3 Clients: Use boto3-stubs (Option 1)

You already have `boto3-stubs[s3]` in dev dependencies! Use it:

```python
# receipt_chroma/receipt_chroma/s3/helpers.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any

def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    s3_client: S3Client | None = None,  # Changed from Optional[Any]
    ...
) -> Dict[str, Any]:
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")
    # Now s3_client is fully typed!
```

### For ChromaDB Collections: Use Protocol (Option 2)

ChromaDB doesn't have type stubs, so Protocol is perfect:

```python
from typing import Protocol, Any, List, Dict

class ChromaCollection(Protocol):
    """Protocol for ChromaDB collection interface."""
    name: str

    def query(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get(self, **kwargs: Any) -> Dict[str, Any]: ...
    def count(self) -> int: ...
    def delete(self, **kwargs: Any) -> None: ...
    def upsert(self, **kwargs: Any) -> None: ...

def get_collection(
    self,
    name: str,
    create_if_missing: bool = False,
    metadata: Dict[str, Any] | None = None,
) -> ChromaCollection:  # Changed from Any
    collection = self._client.get_collection(name)
    return collection  # type: ignore[return-value]  # ChromaDB returns Any
```

## When to Use Each

| Use Case | Use TypedDict | Use Protocol |
|----------|---------------|--------------|
| API response | ✅ | ❌ |
| Configuration object | ✅ | ❌ |
| Function return value (dict) | ✅ | ❌ |
| Object with methods | ❌ | ✅ |
| "Anything that can X" | ❌ | ✅ |
| Mock/test double | ❌ | ✅ |
| External library without stubs | ❌ | ✅ |

## Summary

1. **TypedDict**: For data (dictionaries) - "This dict has these keys with these types"
2. **Protocol**: For behavior (objects) - "This object has these methods"
3. **boto3-stubs**: Already in your dependencies! Use `TYPE_CHECKING` pattern
4. **Protocol for ChromaDB**: Since ChromaDB has no stubs, Protocol is perfect

No new dependencies needed - you already have everything!

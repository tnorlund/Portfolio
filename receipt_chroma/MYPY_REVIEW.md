# Mypy Review for receipt_chroma Package

## Summary

This document reviews mypy type checking results and identifies areas for improvement in type annotations.

## Mypy Errors

### 1. Import Errors (External Dependencies)
These are expected and not fixable without adding stubs to external packages:
- `receipt_dynamo.entities` - missing stubs
- `receipt_dynamo.constants` - missing stubs
- `receipt_dynamo.data.dynamo_client` - missing stubs

**Status**: Acceptable - external dependency issue

### 2. Boto3 Client Creation Issue
**File**: `receipt_chroma/s3/helpers.py:49`
**Error**: `No overload variant of "client" matches argument type "dict[str, str]"`

**Current Code**:
```python
client_kwargs = {"service_name": "s3"}
if region:
    client_kwargs["region_name"] = region
s3_client = boto3.client(**client_kwargs)
```

**Issue**: Mypy can't infer the correct overload when unpacking a dict.

**Fix Options**:
1. Use explicit parameters instead of dict unpacking
2. Add type ignore with specific error code
3. Use `cast()` to help mypy

**Recommended Fix**:
```python
if region:
    s3_client = boto3.client("s3", region_name=region)
else:
    s3_client = boto3.client("s3")
```

## Type Ignore Comments Review

### 1. `chroma_client.py` - Collection Return Types

**Lines**: 420, 445, 478

**Current Code**:
```python
result = collection.query(**query_args)
return result  # type: ignore[no-any-return]

result = collection.get(ids=ids, include=include)
return result  # type: ignore[no-any-return]

return collection.count()  # type: ignore[no-any-return]
```

**Issue**: ChromaDB's collection methods return `Any` because the library doesn't provide proper type stubs.

**Options**:
1. **Keep ignores** - ChromaDB doesn't have proper types, so this is necessary
2. **Create TypedDict definitions** - Define the expected return structure
3. **Use Protocol** - Create a protocol for ChromaDB collections

**Recommendation**: Create TypedDict definitions for query/get results to improve type safety:

```python
from typing import TypedDict

class QueryResult(TypedDict, total=False):
    ids: List[List[str]]
    distances: List[List[float]]
    metadatas: List[List[Dict[str, Any]]]
    documents: List[List[str]]
    embeddings: List[List[List[float]]]

class GetResult(TypedDict, total=False):
    ids: List[str]
    metadatas: List[Dict[str, Any]]
    documents: List[str]
    embeddings: List[List[float]]
```

Then update return types:
```python
def query(...) -> QueryResult:
    result = collection.query(**query_args)
    return result  # type: ignore[assignment]  # ChromaDB returns Any

def get(...) -> GetResult:
    result = collection.get(ids=ids, include=include)
    return result  # type: ignore[assignment]  # ChromaDB returns Any
```

### 2. `poll.py` - Batch Summary Returns

**Lines**: 134, 163

**Current Code**:
```python
return summaries  # type: ignore[no-any-return]
```

**Issue**: The function signature says it returns `List[BatchSummary]`, but mypy thinks `summaries` might be `Any`.

**Fix**: The function already has proper return type annotation. The issue is likely that `get_batch_summaries_by_status` returns `Any`. Check that function's return type.

### 3. `chroma_client.py:558` - Boto3 Import

**Line**: 558
```python
import boto3  # type: ignore[import-untyped]
```

**Status**: Acceptable - boto3 has type stubs but they're incomplete. This ignore is reasonable.

## Unhelpful `Any` Types

### 1. S3 Client Parameters

**Files**: `s3/helpers.py`, `s3/snapshot.py`

**Current**:
```python
s3_client: Optional[Any] = None
```

**Issue**: `Any` hides the actual type. We know it's a boto3 S3 client.

**Fix**:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any

def download_snapshot_from_s3(
    ...
    s3_client: Optional[S3Client] = None,
    ...
) -> Dict[str, Any]:
```

Or use `boto3.client` return type:
```python
from boto3 import client as boto3_client
from typing import cast

# In function
if s3_client is None:
    s3_client = boto3.client("s3")
# Now s3_client is typed as the return type of boto3.client("s3")
```

**Better approach** - Use Protocol:
```python
from typing import Protocol

class S3ClientProtocol(Protocol):
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...
    def upload_file(self, Filename: str, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def list_objects_v2(self, **kwargs: Any) -> Any: ...
    def delete_objects(self, **kwargs: Any) -> Any: ...
    def put_object(self, **kwargs: Any) -> Any: ...
    def get_paginator(self, operation_name: str) -> Any: ...

def download_snapshot_from_s3(
    ...
    s3_client: Optional[S3ClientProtocol] = None,
    ...
):
```

### 2. Context Manager Exit Parameters

**File**: `chroma_client.py:111`

**Current**:
```python
def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
```

**Issue**: These should be properly typed.

**Fix**:
```python
from types import TracebackType
from typing import Optional, Type

def __exit__(
    self,
    exc_type: Optional[Type[BaseException]],
    exc_val: Optional[BaseException],
    exc_tb: Optional[TracebackType],
) -> None:
```

### 3. ChromaDB Client Return Types

**File**: `chroma_client.py:115, 173, 266`

**Current**:
```python
def _ensure_client(self) -> Any:
def client(self) -> Any:
def get_collection(...) -> Any:
```

**Issue**: These return ChromaDB types that don't have proper stubs.

**Options**:
1. **Keep `Any`** - ChromaDB doesn't provide types
2. **Use Protocol** - Define expected interface
3. **Create type aliases** - At least document what they should be

**Recommendation**: Use Protocol for collections:

```python
from typing import Protocol

class ChromaCollection(Protocol):
    def query(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get(self, **kwargs: Any) -> Dict[str, Any]: ...
    def count(self) -> int: ...
    def delete(self, **kwargs: Any) -> None: ...
    def upsert(self, **kwargs: Any) -> None: ...
    name: str

class ChromaClientType(Protocol):
    def get_collection(self, name: str, **kwargs: Any) -> ChromaCollection: ...
    def list_collections(self) -> List[ChromaCollection]: ...
    def create_collection(self, name: str, **kwargs: Any) -> ChromaCollection: ...

def _ensure_client(self) -> ChromaClientType:
def get_collection(...) -> ChromaCollection:
```

### 4. Dictionary Return Types

**Files**: Multiple files return `Dict[str, Any]`

**Examples**:
- `s3/helpers.py`: `-> Dict[str, Any]` (lines 23, 132, 315)
- `s3/snapshot.py`: `-> Dict[str, Any]` (lines 180, 365, 499)
- `embedding/openai/batch_status.py`: Multiple `-> Dict[str, Any]`
- `embedding/metadata/*.py`: `-> Dict[str, Any]`

**Issue**: `Dict[str, Any]` loses type information.

**Recommendation**: Use TypedDict for structured return values:

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

def download_snapshot_from_s3(...) -> DownloadResult:
```

### 5. Metadata Dictionary Types

**Files**: `embedding/metadata/*.py`, `embedding/delta/*.py`

**Current**:
```python
def _parse_metadata_from_line_id(...) -> Dict[str, Any]:
def enrich_line_metadata_with_anchors(
    metadata: Dict[str, Any],
    ...
) -> Dict[str, Any]:
```

**Issue**: Metadata has a known structure but is typed as `Dict[str, Any]`.

**Recommendation**: Define TypedDict for metadata:

```python
class LineMetadata(TypedDict, total=False):
    image_id: str
    receipt_id: int
    line_id: int
    source: str
    anchor_word_ids: List[str]  # Only if anchors exist
    anchor_word_texts: List[str]  # Only if anchors exist
    # ... other known fields
```

## Priority Recommendations

### High Priority
1. **Fix boto3.client() call** in `s3/helpers.py:49` - Use explicit parameters
2. **Fix context manager types** in `chroma_client.py:111` - Use proper exception types
3. **Create TypedDict for return values** - Replace `Dict[str, Any]` with specific types

### Medium Priority
4. **Create Protocol for S3Client** - Replace `Any` with Protocol
5. **Create TypedDict for metadata** - Replace `Dict[str, Any]` with specific metadata types
6. **Review and document ChromaDB return types** - Use Protocols or TypedDict

### Low Priority
7. **Keep ChromaDB collection ignores** - These are necessary until ChromaDB provides types
8. **Review poll.py return types** - Check if `get_batch_summaries_by_status` can be typed

## Files Needing Attention

1. `receipt_chroma/s3/helpers.py` - boto3 client creation, S3Client typing
2. `receipt_chroma/data/chroma_client.py` - Context manager, collection types, return types
3. `receipt_chroma/embedding/metadata/*.py` - Metadata TypedDict definitions
4. `receipt_chroma/embedding/openai/poll.py` - Return type checking
5. `receipt_chroma/s3/snapshot.py` - Return type TypedDict definitions

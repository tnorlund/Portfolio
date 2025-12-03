"""
Practical examples of improving types in receipt_chroma.

This file shows before/after examples - don't actually use this code,
it's just for reference!
"""

# ============================================================================
# EXAMPLE 1: Fixing S3 Client Types (Using boto3-stubs - RECOMMENDED)
# ============================================================================

# BEFORE (current code):
from typing import Any, Optional

def download_snapshot_from_s3(
    bucket: str,
    s3_client: Optional[Any] = None,  # ❌ Any hides the type
) -> Dict[str, Any]:
    if s3_client is None:
        client_kwargs = {"service_name": "s3"}
        s3_client = boto3.client(**client_kwargs)  # ❌ Mypy error
    s3_client.download_file(...)  # ❌ No type checking


# AFTER (using boto3-stubs):
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any  # Only imported during type checking

def download_snapshot_from_s3(
    bucket: str,
    s3_client: S3Client | None = None,  # ✅ Properly typed!
) -> Dict[str, Any]:
    if s3_client is None:
        import boto3
        # Fix the mypy error by using explicit parameters:
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")
        # ✅ Now s3_client is typed as S3Client
    
    s3_client.download_file(...)  # ✅ Full autocomplete and type checking!


# ============================================================================
# EXAMPLE 2: Fixing S3 Client Types (Using Protocol - Alternative)
# ============================================================================

# AFTER (using Protocol - if you prefer):
from typing import Protocol, Any

class S3ClientProtocol(Protocol):
    """Protocol for S3 operations we need."""
    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...
    def upload_file(self, Filename: str, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def get_paginator(self, operation_name: str) -> Any: ...
    def list_objects_v2(self, **kwargs: Any) -> Any: ...
    def delete_objects(self, Bucket: str, Delete: dict) -> Any: ...
    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> Any: ...

def download_snapshot_from_s3(
    bucket: str,
    s3_client: S3ClientProtocol | None = None,  # ✅ Accepts any object with these methods
) -> Dict[str, Any]:
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")  # ✅ Works - real client matches protocol
    
    s3_client.download_file(...)  # ✅ Type checked!


# ============================================================================
# EXAMPLE 3: Fixing Return Types with TypedDict
# ============================================================================

# BEFORE:
def download_snapshot_from_s3(...) -> Dict[str, Any]:  # ❌ Loses type information
    return {
        "status": "downloaded",
        "snapshot_key": "path/to/snapshot",
        "file_count": 42,
        "total_size_bytes": 1024
    }

# AFTER:
from typing import TypedDict, Literal

class DownloadResult(TypedDict, total=False):
    """Result from downloading a snapshot."""
    status: Literal["downloaded", "failed"]  # Only these two values allowed
    snapshot_key: str
    local_path: str
    file_count: int
    total_size_bytes: int
    error: str  # Only present if status == "failed"

def download_snapshot_from_s3(...) -> DownloadResult:  # ✅ Specific type!
    return {
        "status": "downloaded",
        "snapshot_key": "path/to/snapshot",
        "file_count": 42,
        "total_size_bytes": 1024
    }
    # ✅ Mypy checks that all required fields are present
    # ✅ Mypy checks that field types match


# ============================================================================
# EXAMPLE 4: Fixing ChromaDB Collection Types with Protocol
# ============================================================================

# BEFORE:
def get_collection(self, name: str) -> Any:  # ❌ Any hides the type
    collection = self._client.get_collection(name)
    return collection

def query(self, collection_name: str) -> Dict[str, Any]:
    collection = self.get_collection(collection_name)
    result = collection.query(...)  # ❌ No type checking
    return result  # type: ignore[no-any-return]

# AFTER:
from typing import Protocol

class ChromaCollection(Protocol):
    """Protocol for ChromaDB collection interface."""
    name: str
    
    def query(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get(self, **kwargs: Any) -> Dict[str, Any]: ...
    def count(self) -> int: ...
    def delete(self, **kwargs: Any) -> None: ...
    def upsert(self, **kwargs: Any) -> None: ...

def get_collection(self, name: str) -> ChromaCollection:  # ✅ Specific type!
    collection = self._client.get_collection(name)
    return collection  # type: ignore[assignment]  # ChromaDB returns Any, but we know it matches

def query(self, collection_name: str) -> Dict[str, Any]:
    collection = self.get_collection(collection_name)  # ✅ Typed as ChromaCollection
    result = collection.query(...)  # ✅ Type checked - method exists!
    return result  # Still need ignore because ChromaDB returns Any


# ============================================================================
# EXAMPLE 5: Fixing Context Manager Types
# ============================================================================

# BEFORE:
def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:  # ❌ Should be specific
    self.close()

# AFTER:
from types import TracebackType
from typing import Optional, Type

def __exit__(
    self,
    exc_type: Optional[Type[BaseException]],  # ✅ Proper exception type
    exc_val: Optional[BaseException],         # ✅ Proper exception instance
    exc_tb: Optional[TracebackType],         # ✅ Proper traceback type
) -> None:
    self.close()


# ============================================================================
# EXAMPLE 6: Fixing Metadata Types with TypedDict
# ============================================================================

# BEFORE:
def _parse_metadata_from_line_id(custom_id: str) -> Dict[str, Any]:  # ❌ Loses structure
    return {
        "image_id": "uuid",
        "receipt_id": "00001",
        "line_id": "00001",
        "source": "openai_embedding_batch"
    }

# AFTER:
class LineMetadata(TypedDict):
    """Metadata structure for line embeddings."""
    image_id: str
    receipt_id: str
    line_id: str
    source: str

def _parse_metadata_from_line_id(custom_id: str) -> LineMetadata:  # ✅ Specific structure!
    return {
        "image_id": "uuid",
        "receipt_id": "00001",
        "line_id": "00001",
        "source": "openai_embedding_batch"
    }
    # ✅ Mypy ensures all required fields are present
    # ✅ Mypy ensures field types match


# ============================================================================
# SUMMARY: What to Use When
# ============================================================================

"""
1. S3 Client: Use boto3-stubs (you already have it!)
   - Import with TYPE_CHECKING pattern
   - Fix boto3.client() call to use explicit parameters

2. Return Values: Use TypedDict
   - For structured dictionaries returned from functions
   - Use Literal for status strings

3. ChromaDB Collections: Use Protocol
   - ChromaDB has no type stubs
   - Protocol defines the interface you need

4. Context Managers: Use proper exception types
   - Optional[Type[BaseException]] for exc_type
   - Optional[BaseException] for exc_val
   - Optional[TracebackType] for exc_tb

5. Metadata: Use TypedDict
   - Define the structure of metadata dictionaries
   - Use total=False for optional fields
"""

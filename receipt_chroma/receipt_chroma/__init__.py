"""ChromaDB utility package for receipt vector storage."""

from typing import TYPE_CHECKING, Any

from receipt_chroma.section_propagation import Propagation, propagate_knn

__version__ = "0.2.0"

if TYPE_CHECKING:
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_chroma.lock_manager import LockManager

__all__ = [
    "__version__",
    "ChromaClient",
    "LockManager",
    "Propagation",
    "propagate_knn",
]


def __getattr__(name: str) -> Any:
    """Load the stable public classes only when callers request them."""
    if name == "ChromaClient":
        from receipt_chroma.data.chroma_client import ChromaClient

        return ChromaClient
    if name == "LockManager":
        from receipt_chroma.lock_manager import LockManager

        return LockManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

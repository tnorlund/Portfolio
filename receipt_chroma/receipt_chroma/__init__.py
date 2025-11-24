"""ChromaDB utility package for receipt vector storage."""

__version__ = "0.1.0"

from receipt_chroma.data.chroma_client import ChromaClient

__all__ = [
    "__version__",
    "ChromaClient",
]

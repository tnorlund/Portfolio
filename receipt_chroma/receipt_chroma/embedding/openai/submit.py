"""Back-compat re-export. Implementation: receipt_embeddings.openai.submit."""

from receipt_embeddings.openai.submit import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)

__all__ = [
    "add_batch_summary",
    "create_batch_summary",
    "submit_openai_batch",
    "upload_to_openai",
]

"""Back-compat shim: this package moved to ``receipt_embeddings.openai``.

Existing ``receipt_chroma.embedding.openai`` imports (including its
submodules) keep resolving to the relocated modules; new code should import
from ``receipt_embeddings.openai`` (docs/chroma-removal/SPEC.md §6 F).
"""

import sys
from importlib import import_module

from receipt_embeddings.openai import *  # noqa: F401,F403

__all__ = [
    "handle_batch_status",
    "mark_items_for_retry",
    "mark_words_embedded",
    "map_openai_to_dynamo_status",
    "process_error_file",
    "process_partial_results",
    "should_retry_batch",
    "get_unique_receipt_and_image_ids",
    "download_openai_batch_result",
    "get_openai_batch_status",
    "list_pending_line_embedding_batches",
    "list_pending_word_embedding_batches",
    "add_batch_summary",
    "create_batch_summary",
    "submit_openai_batch",
    "upload_to_openai",
    "embed_texts",
]

# Alias the relocated submodules so imports of
# ``receipt_chroma.embedding.openai.<submodule>`` resolve to the very same
# module objects as ``receipt_embeddings.openai.<submodule>``.
batch_status = import_module("receipt_embeddings.openai.batch_status")
helpers = import_module("receipt_embeddings.openai.helpers")
poll = import_module("receipt_embeddings.openai.poll")
realtime = import_module("receipt_embeddings.openai.realtime")
submit = import_module("receipt_embeddings.openai.submit")

sys.modules[__name__ + ".batch_status"] = batch_status
sys.modules[__name__ + ".helpers"] = helpers
sys.modules[__name__ + ".poll"] = poll
sys.modules[__name__ + ".realtime"] = realtime
sys.modules[__name__ + ".submit"] = submit

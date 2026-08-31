"""Back-compat shim: this package moved to ``receipt_embeddings.formatting``.

Existing ``receipt_chroma.embedding.formatting`` imports (including its
submodules) keep resolving to the relocated modules; new code should import
from ``receipt_embeddings.formatting`` (docs/chroma-removal/SPEC.md §6 F).
"""

import sys
from importlib import import_module

from receipt_embeddings.formatting import *  # noqa: F401,F403

__all__ = [
    # Line formatting (row-based)
    "LineLike",
    "group_lines_into_visual_rows",
    "format_visual_row",
    "format_row_embedding_input",
    "get_row_embedding_inputs",
    "get_primary_line_id",
    # Line formatting (legacy)
    "format_line_context_embedding_input",
    "parse_prev_next_from_formatted",
    # Persisted rows and price-column pairing
    "GROUPING_VERSION",
    "WordLike",
    "PriceColumn",
    "LabelAmountPair",
    "is_amount_text",
    "detect_price_column",
    "pair_row_label_amount",
    "build_receipt_rows",
    # Word formatting
    "format_word_context_embedding_input",
    "get_word_neighbors",
]

# Alias the relocated submodules so imports of
# ``receipt_chroma.embedding.formatting.<submodule>`` resolve to the very
# same module objects as ``receipt_embeddings.formatting.<submodule>``.
line_format = import_module("receipt_embeddings.formatting.line_format")
receipt_rows = import_module("receipt_embeddings.formatting.receipt_rows")
word_format = import_module("receipt_embeddings.formatting.word_format")

sys.modules[__name__ + ".line_format"] = line_format
sys.modules[__name__ + ".receipt_rows"] = receipt_rows
sys.modules[__name__ + ".word_format"] = word_format

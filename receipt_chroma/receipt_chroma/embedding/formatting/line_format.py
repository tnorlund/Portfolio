"""Back-compat re-export. Implementation: receipt_embeddings.formatting.line_format."""

from receipt_embeddings.formatting.line_format import (
    LineLike,
    format_line_context_embedding_input,
    format_row_embedding_input,
    format_visual_row,
    get_primary_line_id,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
    parse_prev_next_from_formatted,
)

__all__ = [
    "LineLike",
    "format_line_context_embedding_input",
    "format_row_embedding_input",
    "format_visual_row",
    "get_primary_line_id",
    "get_row_embedding_inputs",
    "group_lines_into_visual_rows",
    "parse_prev_next_from_formatted",
]

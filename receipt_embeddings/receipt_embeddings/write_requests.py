"""Canonical ``EmbeddingWriteRequest`` builder (polish-brief item 3).

Ingest, backfill, resegment, and the correction-flow rewriter all
construct line requests from the visual-row grouping plus phone/address
anchors, and word requests from the terminal-verdict ``label_status``
rule. This is the one implementation; the call sites import it.

Vectors are optional: ingest supplies them from the already-computed
embedding step (``row_embeddings``/``word_embeddings``); backfill looks
them up by canonical key (``known_vectors``); resegment leaves them
unset so the engine writer embeds realtime.

Invariants (corpus contract — do not relax):

- Blank-text rows AND words are SKIPPED: the engine writer refuses
  empty text, and vectors are zipped ``strict`` BEFORE the skip so a
  dropped row drops its vector with it (never misaligns).
- The row's primary ``line_id`` is ``line_ids[0]`` (leftmost line).
- ``section_type`` is stamped only when every mapped line of the row
  agrees; ingest passes no sections and stores ``""`` (the stream
  freshener fills it when RECEIPT_SECTION lands).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal, cast

from receipt_embeddings.formatting import (
    LineLike,
    format_word_context_embedding_input,
    get_row_embedding_inputs,
)
from receipt_embeddings.formatting.word_format import (
    WordLike as ContextWordLike,
)
from receipt_embeddings.keys import line_canonical_key, word_canonical_key
from receipt_embeddings.label_status import WordLabelLike, word_label_statuses
from receipt_embeddings.line_metadata import (
    enrich_row_metadata_with_anchors,
)
from receipt_embeddings.protocols import SectionLike
from receipt_embeddings.writer import EmbeddingWriteRequest

MissingRow = Literal["raise", "skip"]
# The real enricher takes concrete entity types; the seam stays loose on
# parameters (contravariance) and only pins the anchor-mapping result.
AnchorEnricher = Callable[..., Mapping[str, object]]


def _section_by_line(sections: Sequence[SectionLike] | None) -> dict[int, str]:
    result: dict[int, str] = {}
    if not sections:
        return result
    for section in sections:
        for line_id in section.line_ids:
            result[int(line_id)] = str(section.section_type)
    return result


def _section_type_for_row(
    line_ids: Sequence[int], section_by_line: Mapping[int, str]
) -> str:
    if not section_by_line:
        return ""
    values = {section_by_line.get(int(line_id), "") for line_id in line_ids}
    values.discard("")
    return next(iter(values)) if len(values) == 1 else ""


def _row_specs(
    lines: Sequence[LineLike],
    *,
    row_line_ids_list: Sequence[Sequence[int]] | None,
    include_embedding_input: bool,
) -> list[tuple[list[int], str | None]]:
    """Return (line_ids, embedding_input-or-None) for each visual row."""

    if row_line_ids_list is not None:
        if include_embedding_input:
            inputs = {
                tuple(int(value) for value in line_ids): embedding_input
                for embedding_input, line_ids in get_row_embedding_inputs(
                    lines
                )
            }
            specs: list[tuple[list[int], str | None]] = []
            for row_line_ids in row_line_ids_list:
                line_ids = [int(value) for value in row_line_ids]
                specs.append((line_ids, inputs.get(tuple(line_ids))))
            return specs
        return [
            ([int(value) for value in row_line_ids], None)
            for row_line_ids in row_line_ids_list
        ]
    if include_embedding_input:
        return [
            ([int(value) for value in line_ids], embedding_input)
            for embedding_input, line_ids in get_row_embedding_inputs(lines)
        ]
    return [
        ([int(value) for value in line_ids], None)
        for _, line_ids in get_row_embedding_inputs(lines)
    ]


def build_embedding_write_requests(
    *,
    image_id: str,
    receipt_id: int,
    lines: Sequence[LineLike],
    words: Sequence[ContextWordLike],
    word_labels: Sequence[WordLabelLike],
    merchant_name: str = "",
    place_id: str = "",
    sections: Sequence[SectionLike] | None = None,
    row_line_ids_list: Sequence[Sequence[int]] | None = None,
    row_embeddings: Sequence[Sequence[float]] | None = None,
    word_embeddings: Sequence[Sequence[float]] | None = None,
    known_vectors: Mapping[str, Sequence[float]] | None = None,
    include_embedding_input: bool = False,
    missing_row: MissingRow = "raise",
    enrich_anchors: AnchorEnricher | None = None,
) -> list[EmbeddingWriteRequest]:
    """Build line and word ``EmbeddingWriteRequest`` values for one receipt.

    ``row_line_ids_list`` (ingest) is the visual-row grouping that produced
    the in-memory vectors, so rows are never re-derived when it is
    provided. Backfill, resegment, and the correction-flow rewriter omit
    it and use ``get_row_embedding_inputs``. Noise filtering is the
    caller's business — words are taken as given.
    """

    if enrich_anchors is None:
        # The concrete enricher's entity annotations are narrower than the
        # duck seam; the cast is annotation-only (same callable, same call).
        enrich_anchors = cast(AnchorEnricher, enrich_row_metadata_with_anchors)

    lines_by_id = {int(line.line_id): line for line in lines}
    section_by_line = _section_by_line(sections)
    specs = _row_specs(
        lines,
        row_line_ids_list=row_line_ids_list,
        include_embedding_input=include_embedding_input,
    )
    requests: list[EmbeddingWriteRequest] = []
    row_iter: Iterable[
        tuple[tuple[list[int], str | None], Sequence[float] | None]
    ]
    if row_embeddings is not None:
        row_iter = zip(specs, row_embeddings, strict=True)
    else:
        row_iter = ((spec, None) for spec in specs)
    for (line_ids, embedding_input), supplied_row in row_iter:
        row_lines = [
            lines_by_id[line_id]
            for line_id in line_ids
            if line_id in lines_by_id
        ]
        if not row_lines:
            if missing_row == "skip":
                continue
            raise ValueError(
                f"visual row {line_ids} has no matching receipt lines"
            )
        text = " ".join(line.text for line in row_lines)
        if not text.strip():
            continue  # blank OCR rows are unembeddable (writer refuses)
        row_line_id_set = set(line_ids)
        anchors = enrich_anchors(
            {},
            [word for word in words if int(word.line_id) in row_line_id_set],
        )
        primary_line_id = int(line_ids[0])
        vector: Sequence[float] | None = None
        if supplied_row is not None:
            vector = list(supplied_row)
        elif known_vectors is not None:
            vector = known_vectors.get(
                line_canonical_key(image_id, receipt_id, primary_line_id)
            )
        requests.append(
            EmbeddingWriteRequest(
                kind="line",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=primary_line_id,
                text=text,
                embedding_input=embedding_input,
                merchant_name=merchant_name,
                place_id=place_id,
                row_line_ids=tuple(int(value) for value in line_ids),
                section_type=_section_type_for_row(line_ids, section_by_line),
                normalized_phone_10=str(
                    anchors.get("normalized_phone_10", "")
                ),
                normalized_full_address=str(
                    anchors.get("normalized_full_address", "")
                ),
                vector=vector,
            )
        )

    statuses = word_label_statuses(word_labels)
    word_iter: Iterable[tuple[ContextWordLike, Sequence[float] | None]]
    if word_embeddings is not None:
        word_iter = zip(words, word_embeddings, strict=True)
    else:
        word_iter = ((word, None) for word in words)

    for word, supplied in word_iter:
        if not str(word.text).strip():
            continue  # blank OCR words are unembeddable (writer refuses)
        line_id = int(word.line_id)
        word_id = int(word.word_id)
        vector = None
        if supplied is not None:
            vector = list(supplied)
        elif known_vectors is not None:
            vector = known_vectors.get(
                word_canonical_key(image_id, receipt_id, line_id, word_id)
            )
        word_embedding_input = None
        if include_embedding_input:
            word_embedding_input = format_word_context_embedding_input(
                word, words, context_size=2
            )
        requests.append(
            EmbeddingWriteRequest(
                kind="word",
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
                text=word.text,
                embedding_input=word_embedding_input,
                merchant_name=merchant_name,
                label_status=statuses.get((line_id, word_id), "none"),
                vector=vector,
            )
        )
    return requests


__all__ = [
    "AnchorEnricher",
    "MissingRow",
    "build_embedding_write_requests",
]

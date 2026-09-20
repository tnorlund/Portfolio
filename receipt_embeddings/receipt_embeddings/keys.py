"""Canonical vector-item key builders and parsers.

One module so SearchVectors keys, Dynamo PK/SK, and harness IDs cannot
drift. The identity string shared by both backends is:

    IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
    IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
        #WORD#{word_id:05d}  (words append this suffix)

Native DynamoDB embedding items store the same identity as:

    PK = IMAGE#{image_id}
    SK = RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
         [#WORD#{word_id:05d}]#EMBEDDING
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Must match ``receipt_embeddings.service_limits.WORD_INDEX``. This
# module is a leaf (stdlib only) so Lambda handlers and harnesses can
# import it without pulling ``receipt_dynamo.entities``.
_WORD_INDEX = "word-embeddings"

CANONICAL_KEY_RE = re.compile(
    r"^IMAGE#(?P<image_id>[^#]+)#RECEIPT#(?P<receipt_id>[0-9]+)#"
    r"LINE#(?P<line_id>[0-9]+)(?:#WORD#(?P<word_id>[0-9]+))?$"
)
EMBEDDING_SK_RE = re.compile(
    r"^RECEIPT#(?P<receipt_id>[0-9]+)#"
    r"LINE#(?P<line_id>[0-9]+)"
    r"(?:#WORD#(?P<word_id>[0-9]+))?#EMBEDDING$"
)
EMBEDDING_SK_SUFFIX = "#EMBEDDING"


@dataclass(frozen=True, slots=True)
class ParsedCanonicalKey:
    """Identity fields parsed from a canonical key or embedding PK/SK."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int | None = None

    @property
    def is_word(self) -> bool:
        return self.word_id is not None

    def canonical(self) -> str:
        key = line_canonical_key(self.image_id, self.receipt_id, self.line_id)
        if self.word_id is None:
            return key
        return f"{key}#WORD#{self.word_id:05d}"

    def dynamo_key(self) -> dict[str, dict[str, str]]:
        return embedding_item_key(
            self.image_id, self.receipt_id, self.line_id, self.word_id
        )


def line_canonical_key(image_id: str, receipt_id: int, line_id: int) -> str:
    """Canonical line-vector key shared by both backends."""

    return (
        f"IMAGE#{image_id}#RECEIPT#{int(receipt_id):05d}"
        f"#LINE#{int(line_id):05d}"
    )


def word_canonical_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Canonical word-vector key shared by both backends."""

    return (
        f"{line_canonical_key(image_id, receipt_id, line_id)}"
        f"#WORD#{int(word_id):05d}"
    )


# Historical name used by ``similar_labeled_words`` and its tests.
word_vector_key = word_canonical_key


def canonical_key_from_item(item: Mapping[str, Any], *, index: str) -> str:
    """Build a canonical key from a parsed embedding item's attributes."""

    image_id = str(item["image_id"])
    receipt_id = int(item["receipt_id"])
    line_id = int(item["line_id"])
    prefix = line_canonical_key(image_id, receipt_id, line_id)
    if index == _WORD_INDEX:
        return f"{prefix}#WORD#{int(item['word_id']):05d}"
    return prefix


def parse_canonical_key(key: str) -> ParsedCanonicalKey | None:
    """Parse a canonical IMAGE#... key. Returns None if malformed."""

    match = CANONICAL_KEY_RE.fullmatch(key)
    if match is None:
        return None
    values = match.groupdict()
    word_id = values["word_id"]
    return ParsedCanonicalKey(
        image_id=values["image_id"],
        receipt_id=int(values["receipt_id"]),
        line_id=int(values["line_id"]),
        word_id=int(word_id) if word_id is not None else None,
    )


def parse_embedding_pk_sk(pk: str, sk: str) -> ParsedCanonicalKey | None:
    """Parse a native embedding item's PK/SK. Returns None if malformed."""

    if not pk.startswith("IMAGE#"):
        return None
    match = EMBEDDING_SK_RE.fullmatch(sk)
    if match is None:
        return None
    values = match.groupdict()
    word_id = values["word_id"]
    return ParsedCanonicalKey(
        image_id=pk.split("#", 1)[1],
        receipt_id=int(values["receipt_id"]),
        line_id=int(values["line_id"]),
        word_id=int(word_id) if word_id is not None else None,
    )


def embedding_sk(
    receipt_id: int, line_id: int, word_id: int | None = None
) -> str:
    """DynamoDB SK for a native embedding item."""

    sk = f"RECEIPT#{int(receipt_id):05d}#LINE#{int(line_id):05d}"
    if word_id is not None:
        sk += f"#WORD#{int(word_id):05d}"
    return f"{sk}{EMBEDDING_SK_SUFFIX}"


def embedding_item_key(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int | None = None,
) -> dict[str, dict[str, str]]:
    """DynamoDB Key dict for a native embedding item."""

    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": embedding_sk(receipt_id, line_id, word_id)},
    }


def dynamo_key_from_canonical(key: str) -> dict[str, dict[str, str]]:
    """Convert a canonical key to a DynamoDB Key (embedding SK).

    Partition is byte-identical to the backfill verifier: a missing
    ``#RECEIPT#`` separator yields an empty item-part rather than
    raising, so a malformed key still produces a lookup that misses.
    """

    image_part, _, item_part = key.partition("#RECEIPT#")
    return {
        "PK": {"S": image_part},
        "SK": {"S": f"RECEIPT#{item_part}{EMBEDDING_SK_SUFFIX}"},
    }


def canonical_from_dynamo_key(key: Mapping[str, Mapping[str, str]]) -> str:
    """Rebuild the canonical identity from an embedding item Key dict."""

    pk = key["PK"]["S"]
    sk = key["SK"]["S"].removesuffix(EMBEDDING_SK_SUFFIX)
    return f"{pk}#{sk}"


__all__ = [
    "CANONICAL_KEY_RE",
    "EMBEDDING_SK_RE",
    "EMBEDDING_SK_SUFFIX",
    "ParsedCanonicalKey",
    "canonical_from_dynamo_key",
    "canonical_key_from_item",
    "dynamo_key_from_canonical",
    "embedding_item_key",
    "embedding_sk",
    "line_canonical_key",
    "parse_canonical_key",
    "parse_embedding_pk_sk",
    "word_canonical_key",
    "word_vector_key",
]

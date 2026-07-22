"""§7.2 layout-variant selection and ``classifier_hint`` matching.

Single source of truth for the ``template.variants[]`` shape and the
structured ``classifier_hint`` rules defined in
``docs/architecture/MERCHANT_TRUTH_DYNAMO.md`` §7.2. Every consumer of the
variant machinery imports from here so the *generator* (W-G,
``glyphstudio.variant_cluster`` — emits hints) and the *readers* (W-H:
``full_fidelity_eval.profile_columns`` selects a variant,
``merchant_truth_diff.diff_layout`` descends the list) share one
implementation of the selection algorithm — there is no second copy to drift.

A ``classifier_hint`` is a structured rule, never free text::

    {"type": "section_presence" | "section_order" | "text_marker", ...args}

The reader matches a hint against exactly two inputs derived from the receipt
under consideration and *nothing else*:

* the **canonical section sequence** — the receipt's ``RECEIPT_SECTION``
  canonical names in top-to-bottom order — for the ``section_*`` types, and
* the **normalized OCR word set** — every OCR word upper-cased and
  punctuation-stripped — for ``text_marker``.

Selection is deterministic. When more than one variant's hint matches, the
variant with the highest ``support`` wins; equal support breaks ties by the
lexicographically smallest ``variant_id``. No match, no hint, a malformed
hint, or an unrecognized ``type`` falls back to the DEFAULT variant (the
template's top-level ``columns`` / ``sections`` / ``separators``).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Structured classifier_hint type tags (§7.2).
HINT_SECTION_PRESENCE = "section_presence"
HINT_SECTION_ORDER = "section_order"
HINT_TEXT_MARKER = "text_marker"

HINT_TYPES = frozenset(
    {HINT_SECTION_PRESENCE, HINT_SECTION_ORDER, HINT_TEXT_MARKER}
)

# OCR words and hint markers normalize by splitting on every non-alphanumeric
# run and upper-casing. "SELF-CHECKOUT" and a stylescan marker "self_checkout"
# both normalize to the tokens {"SELF", "CHECKOUT"}, so a hint minted from a
# marker matches the receipt regardless of how the OCR split the printed text.
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def normalize_section(name: Any) -> str:
    """Canonical section name comparison key (case/space-insensitive)."""
    return str(name).strip().lower()


def normalize_section_sequence(sequence: Iterable[Any]) -> list[str]:
    """A receipt's canonical section names, normalized, order preserved."""
    return [normalize_section(s) for s in (sequence or []) if str(s).strip()]


def marker_tokens(text: Any) -> list[str]:
    """Split one printed marker / OCR word into normalized tokens."""
    if text is None:
        return []
    return [tok for tok in _TOKEN_SPLIT.split(str(text).upper()) if tok]


def normalize_word_set(words: Iterable[Any]) -> frozenset[str]:
    """Words -> normalized OCR token set (upper-cased, punctuation-stripped).

    Accepts raw strings or word dicts carrying a ``text`` field, so callers
    can pass the eval's word dicts directly.
    """
    tokens: set[str] = set()
    for word in words or ():
        text = word.get("text") if isinstance(word, dict) else word
        tokens.update(marker_tokens(text))
    return frozenset(tokens)


# --- hint builders (used by the W-G generator to emit §7.2 shapes) ---------


def make_section_presence_hint(sections: Iterable[Any]) -> dict:
    return {
        "type": HINT_SECTION_PRESENCE,
        "sections": [normalize_section(s) for s in sections],
    }


def make_section_order_hint(sequence: Iterable[Any]) -> dict:
    return {
        "type": HINT_SECTION_ORDER,
        "sequence": [normalize_section(s) for s in sequence],
    }


def make_text_marker_hint(tokens: Iterable[Any]) -> dict:
    # Deduplicate while keeping a deterministic (sorted) order.
    flat: set[str] = set()
    for tok in tokens:
        flat.update(marker_tokens(tok))
    return {"type": HINT_TEXT_MARKER, "tokens": sorted(flat)}


# --- matching --------------------------------------------------------------


def _is_ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """True when every element of ``needle`` appears in ``haystack`` in that
    relative order (not necessarily contiguous)."""
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needle)


def hint_matches(
    hint: Any,
    *,
    section_sequence: list[str],
    word_set: frozenset[str],
) -> bool:
    """True when ``hint`` matches this receipt's section sequence / word set.

    A missing, malformed, or unrecognized-``type`` hint never matches (the
    caller falls back to DEFAULT). An empty argument list never matches —
    a rule that constrains nothing is not a classifier.
    """
    if not isinstance(hint, dict):
        return False
    htype = hint.get("type")
    if htype == HINT_SECTION_PRESENCE:
        sections = hint.get("sections")
        if not isinstance(sections, list) or not sections:
            return False
        present = set(section_sequence)
        return all(normalize_section(s) in present for s in sections)
    if htype == HINT_SECTION_ORDER:
        sequence = hint.get("sequence")
        if not isinstance(sequence, list) or not sequence:
            return False
        return _is_ordered_subsequence(
            [normalize_section(s) for s in sequence], section_sequence
        )
    if htype == HINT_TEXT_MARKER:
        tokens = hint.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            return False
        return all(t and str(t).upper() in word_set for t in tokens)
    return False


def _support(variant: Any) -> int:
    try:
        return int(variant.get("support", 0))
    except (TypeError, ValueError):
        return 0


def iter_variants(template: Any) -> list[dict]:
    """The template's variant entries (empty when the key is absent/blank)."""
    if not isinstance(template, dict):
        return []
    variants = template.get("variants")
    if not isinstance(variants, list):
        return []
    return [v for v in variants if isinstance(v, dict)]


def select_variant(
    template: Any,
    *,
    section_sequence: Iterable[Any] | None = None,
    word_set: Iterable[Any] | None = None,
) -> dict | None:
    """The variant whose ``classifier_hint`` matches, or ``None`` for DEFAULT.

    ``None`` means the caller should read the template's top-level
    (DEFAULT-variant) ``columns`` / ``sections`` / ``separators``. This is
    always the outcome when ``template.variants`` is absent or empty, so a
    variant-blind bundle behaves exactly as it did before §7.2.

    Tie-break (§7.2): highest ``support`` first, then lexicographically
    smallest ``variant_id`` — fully deterministic.
    """
    variants = iter_variants(template)
    if not variants:
        return None
    seq = normalize_section_sequence(section_sequence or [])
    words = (
        word_set
        if isinstance(word_set, frozenset)
        else frozenset(str(w).upper() for w in (word_set or []))
    )
    matches = [
        v
        for v in variants
        if hint_matches(
            v.get("classifier_hint"), section_sequence=seq, word_set=words
        )
    ]
    if not matches:
        return None
    matches.sort(key=lambda v: (-_support(v), str(v.get("variant_id", ""))))
    return matches[0]


def variant_columns(
    template: Any,
    section: str,
    *,
    section_sequence: Iterable[Any] | None = None,
    word_set: Iterable[Any] | None = None,
) -> list[dict]:
    """Columns for ``section`` from the selected variant, else the DEFAULT.

    When no variant matches (the only possible outcome for a variant-blind
    template) this reads the template's top-level ``columns`` — byte-identical
    to the pre-§7.2 reader.
    """
    variant = select_variant(
        template, section_sequence=section_sequence, word_set=word_set
    )
    source = variant if variant is not None else template
    if not isinstance(source, dict):
        return []
    cols = (source.get("columns") or {}).get(section) or []
    return [dict(c) for c in cols]

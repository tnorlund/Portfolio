"""Deterministic ReceiptWordLabel derivation from the decoded line items.

The band-block decoder already knows, word for word, which words are a
product's name, which word carries its extended price, and which words
carry the quantity and unit price -- ``parse_band`` returns
``name_word_ids`` / ``price_word_id`` / ``qty_word_ids`` alongside every
item it emits. Nothing wrote that knowledge down, so receipts ingested by
the Swift worker end up with header/footer metadata labels and zero
product or financial labels.

This module turns the decode into label proposals. It mints nothing on
its own judgement: every proposal points at a word the decoder already
identified, or at printed text that equals a value the receipt already
stores -- a summary figure to the cent, the ReceiptPlace row's address
or phone, the summary's settled card last four.

The gate is arithmetic, not heuristic. Labels are derived only when the
decoded items reconcile to the receipt's printed baseline as a full
``match`` (``near`` never qualifies, however small the band), which means
the name-to-price pairing is arithmetically proven: change any pairing
and the sum stops landing on the printed figure. ``require_proven``
tightens the gate to the PROVEN tier (``geometry.is_proven``), where the
printed total also agrees with the settled bank amount to the cent.

Writing is deliberately not this module's job -- it returns proposals and
lets the caller decide, so the same derivation serves a backfill script,
an MCP tool and (later) the ingest path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)
from receipt_dynamo.entities.receipt_summary import (
    find_printed_grand_total_words,
    find_printed_subtotal_words,
    find_printed_tax_words,
)

from receipt_upload.line_items.geometry import (
    extract_items,
    is_proven,
    reconcile_detailed,
)
from receipt_upload.line_items.reconstructor import dedupe_grand_total

# The provenance marker for every label this module derives. Distinct from
# every LLM proposer ("llm_valid", "llm_needs_review", "*_analyzer_llm")
# so a later sweep can find, audit or retract exactly this population.
DECODER_PROPOSED_BY = "decoder_reconciled"

# Cent tolerance when matching a printed word's amount to the stored
# summary figure. Both sides are printed money, so this is exact-match
# slack for float representation only, not a comparison band.
_CENT = 0.005

# Floor for "same visual row" when checking that a grand-total election
# actually discriminated between two restatements of the total. Two
# anchored copies printed side by side on one row cannot be told apart by
# any row-ordering rule, so that case still mints nothing.
_MIN_ROW_BAND = 0.005

# Mask glyphs a receipt uses to redact a card number.
_PAN_MASK_CHARS = "*#xX•·"

# The trailing country component of a formatted address, dropped before
# the street/city/state/postal split.
_COUNTRY_TOKENS = frozenset({"usa", "us", "unitedstates"})

# A US phone number, allowing the separators OCR actually preserves.
_PHONE_RE = re.compile(r"(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}")
# Phone-shaped punctuation. Without a merchant phone to verify against, a
# run of digits separated only by spaces is an order number as often as a
# phone, so only a formatted number qualifies.
_PHONE_PUNCT_RE = re.compile(r"[-.()]")
# The longest word span the phone scan will join. A US number never needs
# more than "(702)" "433" "-" "6773".
_PHONE_MAX_SPAN = 4

# Gate verdicts (why a receipt did or did not produce labels).
GATE_OK = "ok"
GATE_NO_ITEMS_SECTION = "no-items-section"
GATE_NO_ITEMS = "no-items"
GATE_NOT_MATCHED = "not-matched"
GATE_COLLAPSED_BANDING = "collapsed-banding"
GATE_NOT_PROVEN = "not-proven"


@dataclass(frozen=True)
class DerivedLabel:
    """One label proposal: a word, a label, and why the decoder said so."""

    line_id: int
    word_id: int
    label: str
    reasoning: str
    text: str = ""

    @property
    def word_key(self) -> tuple[int, int]:
        """The (line_id, word_id) this proposal targets."""
        return (self.line_id, self.word_id)


@dataclass
class DerivationResult:
    """The outcome of deriving labels for one receipt."""

    gate: str
    labels: list[DerivedLabel] = field(default_factory=list)
    reconciliation_status: Optional[str] = None
    baseline_figures_agreeing: Optional[int] = None
    item_sum: Optional[float] = None
    baseline: Optional[float] = None
    item_count: int = 0

    @property
    def derived(self) -> bool:
        """Whether the receipt passed the gate and produced proposals."""
        return self.gate == GATE_OK


def _word_key(ref: Any) -> Optional[tuple[int, int]]:
    """Normalize a decoder word reference to (line_id, word_id)."""
    if not ref:
        return None
    try:
        return (int(ref["line_id"]), int(ref["word_id"]))
    except (KeyError, TypeError, ValueError):
        return None


def _to_geometry_word(word: Any) -> Optional[dict[str, Any]]:
    """Adapt a ReceiptWord entity to the decoder's word-dict shape."""
    box = getattr(word, "bounding_box", None) or {}
    try:
        height = float(box.get("height", 0.0))
        return {
            "line_id": int(word.line_id),
            "word_id": int(word.word_id),
            "text": str(word.text),
            "x": float(box.get("x", 0.0)),
            "y_mid": float(box.get("y", 0.0)) + height / 2.0,
            "h": height,
        }
    except (AttributeError, TypeError, ValueError):
        return None


def _has_letters(text: str) -> bool:
    """Whether a token carries alphabetic content."""
    return bool(re.search(r"[A-Za-z]", text or ""))


def _amount_of(text: str) -> Optional[float]:
    """Parse a word as a printed amount, or None."""
    if not looks_like_receipt_amount(text):
        return None
    return parse_receipt_amount(text)


# An amount whose decoration -- not its digits -- OCR mangled. Vision
# reads "$" as "S", and receipts print tax flags ("15.59T") that no
# currency parser expects. The prefix deliberately excludes sign
# characters, so a negative or accounting-parenthesised amount is never
# silently read as positive; those forms are well-formed and belong to
# the strict reading.
_OCR_DAMAGED_AMOUNT_RE = re.compile(
    r"^[^\d.,+\-]{0,3}(\d{1,4}[.,]\d{2})[A-Za-z*]?$"
)


def _ocr_damaged_amount_of(text: str) -> Optional[float]:
    """Parse an amount whose currency glyph or tax flag OCR mangled.

    Deliberately NOT a replacement for :func:`_amount_of`. Where a
    word's shape is the only evidence that it is money, rejecting
    "S0.49" for its leading letter is right. This reading is safe only
    where something else already vouches for the value -- see
    :func:`_quantity_labels`, whose caller has arithmetic proof.
    """
    match = _OCR_DAMAGED_AMOUNT_RE.match((text or "").strip())
    return parse_receipt_amount(match.group(1)) if match else None


def _keys_worth(
    keys: Sequence[tuple[int, int]],
    texts: dict[tuple[int, int], str],
    value: float,
    parse: Any,
) -> list[tuple[int, int]]:
    """Span words whose parsed amount equals ``value`` to the cent."""
    return [
        key
        for key in keys
        if (amount := parse(texts.get(key, ""))) is not None
        and abs(amount - value) < _CENT
    ]


def _numeric_of(text: str) -> Optional[float]:
    """Parse a word as a bare number ("2", "2x", "1.31"), or None."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)", text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _summary_value(summary: Optional[dict], key: str) -> Optional[float]:
    """Read one numeric figure off the stored receipt summary."""
    if not summary:
        return None
    value = summary.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _item_labels(
    item: dict, texts: dict[tuple[int, int], str]
) -> list[DerivedLabel]:
    """Label proposals for one decoded item."""
    out: list[DerivedLabel] = []
    name = item.get("name") or ""
    price = item.get("price")
    is_discount = bool(item.get("is_discount"))

    # The extended price word. A discount row's price IS the discount
    # amount, so it takes DISCOUNT rather than LINE_TOTAL -- labelling it
    # LINE_TOTAL would teach the model that a negative line total is
    # normal and would double-count in every downstream sum.
    price_key = _word_key(item.get("price_word_id"))
    if price_key is not None and price is not None:
        label = "DISCOUNT" if is_discount else "LINE_TOTAL"
        out.append(
            DerivedLabel(
                line_id=price_key[0],
                word_id=price_key[1],
                label=label,
                reasoning=(
                    f"Decoder paired this amount with item {name!r} at "
                    f"{price:.2f}; the receipt's items reconcile to its "
                    "printed baseline"
                ),
                text=texts.get(price_key, ""),
            )
        )

    # Product-name words. "low" name quality means the decoder recovered a
    # price with no readable name, so there is nothing to name. Words with
    # no letters are skipped: PRODUCT_NAME is descriptive text, and a bare
    # numeric token in a name span is a SKU echo or an unrecognized
    # quantity, never a product name.
    if not is_discount and item.get("name_quality") != "low" and name:
        priced = f" priced at {price:.2f}" if price is not None else ""
        reasoning = (
            f"Decoder read this word as part of the product name "
            f"{name!r}{priced}; the receipt's items reconcile to its "
            "printed baseline"
        )
        seen: set[tuple[int, int]] = set()
        for ref in item.get("name_word_ids") or []:
            key = _word_key(ref)
            if key is None or key in seen:
                continue
            seen.add(key)
            text = texts.get(key, "")
            if not _has_letters(text):
                continue
            out.append(
                DerivedLabel(
                    line_id=key[0],
                    word_id=key[1],
                    label="PRODUCT_NAME",
                    reasoning=reasoning,
                    text=text,
                )
            )

    out.extend(_quantity_labels(item, texts))
    return out


def _quantity_labels(
    item: dict, texts: dict[tuple[int, int], str]
) -> list[DerivedLabel]:
    """QUANTITY / UNIT_PRICE proposals from the decoder's quantity span.

    ``qty_word_ids`` covers the whole quantity expression ("2 @ 8.99",
    a bare leading "3"), so the two roles are separated by value: the word
    whose printed amount equals the parsed unit price is UNIT_PRICE, and
    the word whose number equals the parsed quantity is QUANTITY. When a
    role has no unambiguous single word, nothing is minted for it -- the
    span is evidence, not a guess.

    Tolerant in parsing, strict in acceptance. The decoder only supplies
    ``unit_price`` when ``quantity x unit_price`` reproduces the item's
    printed price to the cent, so by the time a word reaches here the
    value is arithmetically proven and the word is one the decoder
    itself pointed at. Refusing to label it because OCR read "$0.49" as
    "S0.49" would be re-deriving trust from the glyph shape after the
    arithmetic already settled it. So when no well-formed word in the
    span carries the value, the span is read again allowing OCR-damaged
    decoration. Acceptance never widens: the amount must still equal the
    proven unit price to the cent, and still be the span's only word
    that does.
    """
    quantity = item.get("quantity")
    unit_price = item.get("unit_price")
    keys = [
        key
        for key in (_word_key(ref) for ref in item.get("qty_word_ids") or [])
        if key is not None
    ]
    if not keys or quantity is None:
        return []

    unit_keys: list[tuple[int, int]] = []
    if unit_price is not None:
        unit_keys = _keys_worth(keys, texts, unit_price, _amount_of)
        if not unit_keys:
            unit_keys = _keys_worth(
                keys, texts, unit_price, _ocr_damaged_amount_of
            )
    qty_keys = [
        key
        for key in keys
        if key not in unit_keys
        and (value := _numeric_of(texts.get(key, ""))) is not None
        and abs(value - quantity) < 1e-9
    ]

    out: list[DerivedLabel] = []
    if len(qty_keys) == 1:
        key = qty_keys[0]
        out.append(
            DerivedLabel(
                line_id=key[0],
                word_id=key[1],
                label="QUANTITY",
                reasoning=(
                    f"Decoder parsed quantity {quantity:g} for item "
                    f"{item.get('name') or ''!r} from this word"
                ),
                text=texts.get(key, ""),
            )
        )
    if len(unit_keys) == 1 and unit_price is not None:
        key = unit_keys[0]
        out.append(
            DerivedLabel(
                line_id=key[0],
                word_id=key[1],
                label="UNIT_PRICE",
                reasoning=(
                    f"Decoder parsed unit price {unit_price:.2f} x "
                    f"{quantity:g} for item {item.get('name') or ''!r}"
                ),
                text=texts.get(key, ""),
            )
        )
    return out


@dataclass
class _ElectionCandidate:
    """A stand-in GRAND_TOTAL label, for ``dedupe_grand_total`` to elect.

    ``dedupe_grand_total`` decides which of a receipt's restatements of
    the total is canonical, reading only these four fields off each
    label. Handing it stand-ins for the words we are *about* to label
    runs the corpus's own election rule over our proposals, instead of
    inventing a second one that could disagree with it.
    """

    line_id: int
    word_id: int
    label: str = "GRAND_TOTAL"
    validation_status: str = "PENDING"


def _y_center(word: Any) -> Optional[float]:
    """Normalized y-center of a word's bounding box, or None."""
    box = getattr(word, "bounding_box", None) or {}
    try:
        return float(box.get("y", 0.0)) + float(box.get("height", 0.0)) / 2.0
    except (TypeError, ValueError):
        return None


def _row_band(word: Any) -> float:
    """Half-row tolerance around a word, in normalized units."""
    box = getattr(word, "bounding_box", None) or {}
    try:
        height = float(box.get("height", 0.0))
    except (TypeError, ValueError):
        height = 0.0
    return max(0.6 * height, _MIN_ROW_BAND)


def _elect_grand_total_word(
    receipt_words: Sequence[Any], candidates: Sequence[Any]
) -> Optional[Any]:
    """Pick the one word that carries the receipt's grand total.

    Receipts restate the total: Trader Joe's prints it against "Balance
    to pay" on the store copy and again against "TOTAL PURCHASE" on the
    card slip, and both are legitimate grand-total anchors. A receipt has
    exactly one grand total, and ``reconstructor.dedupe_grand_total``
    already decides which restatement is canonical and invalidates the
    rest -- so labelling every copy would mint labels the pipeline's own
    dedupe pass then has to retract, and would teach a model that echoes
    of the total are grand totals. We label the copy dedupe would keep.

    Still fail-closed where the anchor genuinely cannot discriminate:
    if the election leaves more than one survivor, or if a losing copy
    shares the winner's visual row (so no row-ordering rule could have
    separated them), nothing is minted.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    stand_ins = [
        _ElectionCandidate(int(w.line_id), int(w.word_id)) for w in candidates
    ]
    redundant = {
        id(x)
        for x in dedupe_grand_total(
            list(receipt_words), stand_ins  # type: ignore[arg-type]
        )
    }
    survivors = [
        word
        for word, stand_in in zip(candidates, stand_ins)
        if id(stand_in) not in redundant
    ]
    if len(survivors) != 1:
        return None
    elected = survivors[0]

    elected_y = _y_center(elected)
    if elected_y is None:
        return None
    band = _row_band(elected)
    for other in candidates:
        if other is elected:
            continue
        other_y = _y_center(other)
        if other_y is None or abs(other_y - elected_y) <= band:
            return None
    return elected


def _summary_labels(
    receipt_words: Sequence[Any], summary: Optional[dict]
) -> list[DerivedLabel]:
    """GRAND_TOTAL / SUBTOTAL / TAX proposals for printed summary words.

    A word is labelled only when it sits on a summary anchor row AND its
    printed amount equals the receipt's stored summary figure to the
    cent. GRAND_TOTAL tolerates the figure being restated -- receipts
    print the total against several anchors -- and elects the canonical
    copy via :func:`_elect_grand_total_word`. SUBTOTAL and TAX have no
    such election rule, so for them two words printing the same figure
    stay ambiguous, and ambiguity mints nothing.
    """
    out: list[DerivedLabel] = []
    finders = (
        ("GRAND_TOTAL", "grand_total", find_printed_grand_total_words),
        ("SUBTOTAL", "subtotal", find_printed_subtotal_words),
        ("TAX", "tax", find_printed_tax_words),
    )
    for label, key, finder in finders:
        value = _summary_value(summary, key)
        if value is None:
            continue
        matches = [
            word
            for amount, word in finder(list(receipt_words))
            if abs(amount - value) < _CENT
        ]
        unique = {(int(w.line_id), int(w.word_id)): w for w in matches}
        if label == "GRAND_TOTAL":
            word = _elect_grand_total_word(
                receipt_words, list(unique.values())
            )
            if word is None:
                continue
            line_id, word_id = int(word.line_id), int(word.word_id)
        else:
            if len(unique) != 1:
                continue
            (line_id, word_id), word = next(iter(unique.items()))
        out.append(
            DerivedLabel(
                line_id=line_id,
                word_id=word_id,
                label=label,
                reasoning=(
                    f"Printed {label.replace('_', ' ').lower()} row carries "
                    f"{value:.2f}, matching the receipt summary; the "
                    "decoded items reconcile to the printed baseline"
                ),
                text=str(word.text),
            )
        )
    return out


def _norm_token(text: str) -> str:
    """Lowercase alphanumeric form of a word ("Henderson," -> henderson)."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _tokens(text: str) -> list[str]:
    """Normalized tokens of a phrase, dropping punctuation-only words."""
    return [t for t in (_norm_token(part) for part in text.split()) if t]


@dataclass(frozen=True)
class _PlaceAddress:
    """The verified address, split into the parts that anchor a match."""

    tokens: frozenset[str]
    street_number: Optional[str]
    street_names: frozenset[str]
    city: frozenset[str]
    state: frozenset[str]
    postal: frozenset[str]


def _parse_place_address(place: Optional[dict]) -> Optional[_PlaceAddress]:
    """Split the ReceiptPlace ``formatted_address`` into match anchors."""
    address = (place or {}).get("formatted_address") or ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if len(parts) < 2:
        return None
    if _norm_token(parts[-1]) in _COUNTRY_TOKENS:
        parts = parts[:-1]
    if len(parts) < 2:
        return None

    street = _tokens(parts[0])
    number = street[0] if street and street[0].isdigit() else None
    postal, state = set(), set()
    for token in _tokens(parts[-1]):
        if re.fullmatch(r"\d{5}(?:\d{4})?", token):
            postal.add(token)
        elif len(token) == 2 and token.isalpha():
            state.add(token)
    city = (
        {t for t in _tokens(parts[-2]) if t.isalpha() and len(t) >= 3}
        if len(parts) >= 3
        else set()
    )
    return _PlaceAddress(
        tokens=frozenset(t for part in parts for t in _tokens(part)),
        street_number=number,
        # One- and two-letter street tokens ("N", "St") are too common to
        # anchor on; the distinctive part of a street name is the rest.
        street_names=frozenset(
            t for t in street if t.isalpha() and len(t) >= 3
        ),
        city=frozenset(city),
        state=frozenset(state),
        postal=frozenset(postal),
    )


def _is_address_line(tokens: Sequence[str], addr: _PlaceAddress) -> bool:
    """Whether a line's tokens anchor onto the verified place address.

    Three anchors, each distinctive enough to stand alone: the street
    number printed with a street-name word, the postal code, or the city
    printed with the state. Position on the receipt is deliberately not
    consulted -- the header is not always the geometric top line, and the
    place row is the authority either way.
    """
    seen = set(tokens)
    street_hit = (
        addr.street_number is not None
        and addr.street_number in seen
        and bool(seen & addr.street_names)
    )
    postal_hit = bool(seen & addr.postal)
    city_hit = bool(seen & addr.city) and (
        bool(seen & addr.state) or postal_hit
    )
    return street_hit or postal_hit or city_hit


def _address_labels(
    lines: dict[int, list[Any]], place: Optional[dict]
) -> list[DerivedLabel]:
    """ADDRESS_LINE proposals for lines that match the ReceiptPlace row.

    The whole matched line is labelled, not just the words that echo the
    place record: "2716 North Green Valley Par way" is one address line
    even though Places spells it "2716 N Green Valley Pkwy" and OCR broke
    "Parkway" in half. To keep that from swallowing a neighbour, every
    word on the line must be address-plausible -- an address token, a
    word, or a short number. One phone number or price on the row and the
    whole line is declined.
    """
    addr = _parse_place_address(place)
    if addr is None:
        return []

    out: list[DerivedLabel] = []
    for line_id in sorted(lines):
        words = sorted(lines[line_id], key=lambda w: int(w.word_id))
        tokens = [_norm_token(str(w.text)) for w in words]
        present = [t for t in tokens if t]
        if not present or not _is_address_line(present, addr):
            continue
        if not all(
            token in addr.tokens
            or token.isalpha()
            or (token.isdigit() and len(token) <= 5)
            for token in present
        ):
            continue
        for word, token in zip(words, tokens):
            if not token:
                continue
            out.append(
                DerivedLabel(
                    line_id=int(word.line_id),
                    word_id=int(word.word_id),
                    label="ADDRESS_LINE",
                    reasoning=(
                        "Line matches the receipt's verified place address "
                        f"{(place or {}).get('formatted_address')!r}"
                    ),
                    text=str(word.text),
                )
            )
    return out


def _phone_spans(words: Sequence[Any]) -> list[list[Any]]:
    """Maximal word spans on one line that read as a phone number."""
    spans: list[tuple[int, int]] = []
    for start in range(len(words)):
        for end in range(min(start + _PHONE_MAX_SPAN, len(words)), start, -1):
            text = " ".join(str(w.text) for w in words[start:end])
            if _PHONE_RE.fullmatch(text.strip()):
                spans.append((start, end))
                break
    maximal = [
        (start, end)
        for start, end in spans
        if not any(
            (s, e) != (start, end) and s <= start and end <= e
            for s, e in spans
        )
    ]
    return [list(words[start:end]) for start, end in maximal]


def _phone_labels(
    lines: dict[int, list[Any]], place: Optional[dict]
) -> list[DerivedLabel]:
    """PHONE_NUMBER proposals for the merchant's printed phone number.

    Verified against the place row when it carries a phone, and otherwise
    held to a strict printed format plus receipt-wide uniqueness.

    OCR drops digits from phone numbers often enough that "702 433-67 3"
    has to decline rather than be repaired: nine digits is not a phone
    number, and a repaired guess would be indistinguishable in the corpus
    from a real one.
    """
    verified = _digits(
        (place or {}).get("phone_number") or (place or {}).get("phone_intl")
    )
    candidates: list[list[Any]] = []
    for line_id in sorted(lines):
        words = sorted(lines[line_id], key=lambda w: int(w.word_id))
        candidates.extend(_phone_spans(words))

    if verified and len(verified) >= 10:
        spans = [
            span
            for span in candidates
            if _digits(" ".join(str(w.text) for w in span))[-10:]
            == verified[-10:]
        ]
        reason = (
            "Printed phone number matches the receipt's verified place "
            f"record ({(place or {}).get('phone_number')!r})"
        )
    else:
        spans = [
            span
            for span in candidates
            if _PHONE_PUNCT_RE.search(" ".join(str(w.text) for w in span))
        ]
        # Nothing external to check an unverified number against, so a
        # receipt printing two different phone-shaped numbers is
        # ambiguous and mints neither.
        if len(spans) != 1:
            return []
        reason = "Printed in the format of a phone number"

    out: list[DerivedLabel] = []
    for span in spans:
        for word in span:
            out.append(
                DerivedLabel(
                    line_id=int(word.line_id),
                    word_id=int(word.word_id),
                    label="PHONE_NUMBER",
                    reasoning=reason,
                    text=str(word.text),
                )
            )
    return out


def _digits(value: Any) -> str:
    """Every digit in a value, in order."""
    return re.sub(r"\D", "", str(value or ""))


def _masked_last4(text: str) -> Optional[str]:
    """Last four digits of a masked card number, or None.

    The mask has to do the work: a word qualifies only when its digits
    are exactly four and terminal, behind at least four mask glyphs.
    "MID: *******04690" (five trailing digits) and a bare "1454" both
    fail, which is what keeps a merchant id or an item count out.
    """
    match = re.fullmatch(r"([^0-9]*)(\d{4})", (text or "").strip())
    if match is None:
        return None
    masks = sum(1 for ch in match.group(1) if ch in _PAN_MASK_CHARS)
    return match.group(2) if masks >= 4 else None


def _masked_pan_labels(
    receipt_words: Sequence[Any], summary: Optional[dict]
) -> list[DerivedLabel]:
    """PAYMENT_METHOD for the masked PAN the summary settled against.

    Deliberately not ``CARD_NUMBER``. That reads like the more precise
    label and an LLM proposer reaches for it, but it is not in
    ``CORE_LABELS``: ``label_normalization.NON_CORE_LABEL_ALIASES`` maps
    CARD_NUMBER onto PAYMENT_METHOD, whose definition ("payment
    instrument summary, e.g. VISA ••••1234") is exactly a masked PAN.
    Minting the alias would put a label outside the canonical vocabulary
    into the corpus that every consumer then has to normalize back.
    """
    last4 = str((summary or {}).get("card_last4") or "").strip()
    if not re.fullmatch(r"\d{4}", last4):
        return []
    matches = [
        word
        for word in receipt_words
        if _masked_last4(str(getattr(word, "text", ""))) == last4
    ]
    # A receipt printing the masked PAN twice cannot say which copy the
    # summary read, so it mints neither.
    if len(matches) != 1:
        return []
    word = matches[0]
    return [
        DerivedLabel(
            line_id=int(word.line_id),
            word_id=int(word.word_id),
            label="PAYMENT_METHOD",
            reasoning=(
                "Masked card number ending "
                f"{last4}, matching the receipt summary's settled card"
            ),
            text=str(word.text),
        )
    ]


def _header_labels(
    receipt_words: Sequence[Any],
    summary: Optional[dict],
    place: Optional[dict],
) -> list[DerivedLabel]:
    """ADDRESS_LINE / PHONE_NUMBER / PAYMENT_METHOD from stored records.

    None of these come from the decode. Each is checked against a record
    the receipt already carries -- the ReceiptPlace row for the address
    and phone, the receipt summary's settled card for the masked PAN --
    so the derivation stays a lookup rather than a judgement, and
    declines when the record is missing or disagrees.
    """
    lines: dict[int, list[Any]] = {}
    for word in receipt_words:
        try:
            lines.setdefault(int(word.line_id), []).append(word)
        except (AttributeError, TypeError, ValueError):
            continue
    return [
        *_address_labels(lines, place),
        *_phone_labels(lines, place),
        *_masked_pan_labels(receipt_words, summary),
    ]


def derive_labels(
    receipt_words: Sequence[Any],
    items_line_ids: Iterable[int],
    summary: Optional[dict],
    *,
    require_proven: bool = False,
    printed_total: Optional[float] = None,
    bank_amount: Optional[float] = None,
    place: Optional[dict] = None,
) -> DerivationResult:
    """Derive word labels from the reconciled line-item decode.

    Args:
        receipt_words: The receipt's ``ReceiptWord`` entities.
        items_line_ids: Line ids of the receipt's ITEMS section.
        summary: The stored receipt summary ({subtotal, tax, grand_total,
            ...}), used both as the reconciliation baseline and as the
            cross-check for the printed summary figures.
        require_proven: Tighten the gate to the PROVEN tier -- the
            printed total must also agree with the settled bank amount to
            the cent (``geometry.is_proven``).
        printed_total: The printed grand total, for the PROVEN check.
            Defaults to the summary's grand total.
        bank_amount: The settled bank amount, for the PROVEN check.
        place: The receipt's stored ``ReceiptPlace`` row, whose
            ``formatted_address`` and ``phone_number`` verify the header
            proposals. Without it no header label is derived.

    Returns:
        A :class:`DerivationResult`. ``labels`` is empty unless ``gate``
        is :data:`GATE_OK`.
    """
    line_ids = {int(x) for x in items_line_ids}
    if not line_ids:
        return DerivationResult(GATE_NO_ITEMS_SECTION)

    words = [w for w in (_to_geometry_word(w) for w in receipt_words) if w]
    texts = {(w["line_id"], w["word_id"]): w["text"] for w in words}

    items, collapsed = extract_items(words, line_ids, summary=summary)
    recon = reconcile_detailed(
        [item for item in items if not item.get("is_discount")], summary
    )
    result = DerivationResult(
        gate=GATE_OK,
        reconciliation_status=recon.status,
        baseline_figures_agreeing=recon.baseline_figures_agreeing,
        item_sum=recon.item_sum,
        baseline=recon.baseline,
        item_count=len(items),
    )

    if not items:
        result.gate = GATE_NO_ITEMS
        return result
    if recon.status != "match":
        result.gate = GATE_NOT_MATCHED
        return result
    # Degenerate banding merged several prices into one band, so the
    # word-to-item mapping is not trustworthy even when the sum lands.
    if collapsed:
        result.gate = GATE_COLLAPSED_BANDING
        return result
    if require_proven:
        total = (
            printed_total
            if printed_total is not None
            else _summary_value(summary, "grand_total")
        )
        if not is_proven(recon.status, total, bank_amount):
            result.gate = GATE_NOT_PROVEN
            return result

    labels: list[DerivedLabel] = []
    for item in items:
        labels.extend(_item_labels(item, texts))
    labels.extend(_summary_labels(receipt_words, summary))
    labels.extend(_header_labels(receipt_words, summary, place))

    # One word, one derived label. A word the decoder claims twice (a
    # price that is also a summary figure) is ambiguous, and ambiguity
    # mints nothing.
    by_word: dict[tuple[int, int], list[DerivedLabel]] = {}
    for proposal in labels:
        by_word.setdefault(proposal.word_key, []).append(proposal)
    result.labels = [
        proposals[0]
        for _, proposals in sorted(by_word.items())
        if len({p.label for p in proposals}) == 1
    ]
    return result


__all__ = [
    "DECODER_PROPOSED_BY",
    "DerivationResult",
    "DerivedLabel",
    "GATE_COLLAPSED_BANDING",
    "GATE_NOT_MATCHED",
    "GATE_NOT_PROVEN",
    "GATE_NO_ITEMS",
    "GATE_NO_ITEMS_SECTION",
    "GATE_OK",
    "derive_labels",
]

"""Shared receipt-reconciliation passes for synthetic-candidate flattening.

This module is the single canonical home for the passes that clean a flattened
synthetic receipt candidate -- ``(tokens, bboxes, ner_tags)`` parallel arrays --
so it survives the objective verifier (``synthesis_loop/verify_candidates.py``).
Both ``sprouts_parameterization.py`` and ``merchant_synthesis.py`` import from
here, so a fix made once is automatically applied to every merchant.

The label-sanitization vocabulary and violation tests are byte-equivalent to the
verifier's ``_label_violations`` / ``_entity_runs`` so the demote-to-O pass
removes precisely the runs the objective ``labels_valid`` check would flag.

Public surface:
  - ``reconcile_candidate(tokens, bboxes, ner_tags, merchant_name=None)`` --
    convenience entry that runs every pass in the canonical order and returns the
    cleaned ``(tokens, bboxes, ner_tags)``.
  - The individual passes / helpers / constants are exported too, for callers
    that still reference them directly:
    ``_tag_label``, ``_label_runs``, ``_dedupe_header_blocks``,
    ``_dedupe_payment_blocks``, ``_deoverlap_line_words``, ``_respace_visual_line``,
    ``_visual_lines``, ``_sanitize_entity_labels``, ``_verifier_entity_runs``,
    ``_entity_run_violates``, ``_card_tail``, ``_has_digit``,
    ``_merchant_name_tokens`` and their backing constants.
"""

from __future__ import annotations

import re
from typing import Any

from .synthesis_text_clean import clean_token_text


# --- BIO tag helpers ------------------------------------------------------------
def _tag_label(tag: str) -> str:
    """Strip the BIO prefix from a tag, returning the bare label (or 'O')."""
    if not tag or tag == "O":
        return "O"
    return tag.split("-", 1)[-1]


def _label_runs(tags: list[str], label: str) -> list[tuple[int, int]]:
    """Return [start, end) index ranges of maximal contiguous `label` blocks."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(tags)
    while i < n:
        if _tag_label(tags[i]) == label:
            j = i
            while j < n and _tag_label(tags[j]) == label:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


# --- content-aware label sanitization (verify_candidates.py-equivalent) ---------
# Mirrors `synthesis_loop/verify_candidates.py`'s `_label_violations` /
# `_entity_runs` so the demote-to-O pass removes precisely the runs the objective
# `labels_valid` check would flag. Local copy (no import); byte-equivalent vocab.
_VERIFIER_TENDER = {
    "DEBIT", "CREDIT", "VISA", "MASTERCARD", "MC", "AMEX", "DISCOVER", "CASH",
    "US", "CHECK", "EBT", "CONTACTLESS", "CHIP", "SWIPE", "CARD", "TENDER",
    "PIN", "ATM",
}
_VERIFIER_PHONE = re.compile(
    r"\(\d{3}\)\s?\d{3}[- ]?\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4}\b|\b\d{3}[-.]\d{4}\b"
)
_VERIFIER_DOMAIN = re.compile(r"[A-Za-z0-9-]+\.(?:com|net|org|co|us|gov)\b", re.I)
_VERIFIER_CURRENCYISH = re.compile(r"-?\$?\d+\.\d{2}-?$")
_VERIFIER_MONEY = {
    "LINE_TOTAL", "GRAND_TOTAL", "SUBTOTAL", "TAX", "UNIT_PRICE", "BALANCE_DUE",
}
_VERIFIER_CONTENT_LABELS = _VERIFIER_MONEY | {
    "PAYMENT_METHOD", "PHONE_NUMBER", "WEBSITE",
}


def _verifier_entity_runs(tags: list[str]) -> list[tuple[str, int, int]]:
    """Contiguous BIO spans -> (label, start, end) inclusive.

    Mirrors verify_candidates.py `_entity_runs`: a same-label token that begins a
    fresh ``B-`` starts a NEW run (so two adjacent entities are not merged).
    """
    runs: list[tuple[str, int, int]] = []
    i, n = 0, len(tags)
    while i < n:
        lab = _tag_label(tags[i])
        if lab == "O":
            i += 1
            continue
        j = i
        while (
            j + 1 < n
            and _tag_label(tags[j + 1]) == lab
            and not str(tags[j + 1]).startswith("B-")
        ):
            j += 1
        runs.append((lab, i, j))
        i = j + 1
    return runs


def _entity_run_violates(lab: str, text: str) -> bool:
    """True when a run's joined text can't be its label (verifier `_label_violations`)."""
    joined = text.replace(" ", "")
    if not text:
        return False
    if lab in _VERIFIER_MONEY:
        return not _VERIFIER_CURRENCYISH.match(joined.replace(",", ""))
    if lab == "PAYMENT_METHOD":
        up = text.upper()
        return not (
            any(w in up for w in _VERIFIER_TENDER)
            or re.search(r"[X*]{3,}\w", joined)
        )
    if lab == "PHONE_NUMBER":
        return not _VERIFIER_PHONE.search(text) and not _VERIFIER_PHONE.search(joined)
    if lab == "WEBSITE":
        return not _VERIFIER_DOMAIN.search(joined) and not re.search(
            r"[A-Za-z]{3,}", text
        )
    return False


def _sanitize_entity_labels(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> tuple[list[str], list[list[int]], list[str]]:
    """Demote-to-O every BIO entity run whose content violates its label.

    A wrong entity label is worse than no label for LayoutLM, so each contiguous
    run that the objective `labels_valid` check would flag (a money field that
    isn't currency, a PAYMENT_METHOD with no tender word / mask, a PHONE_NUMBER
    that isn't a phone, a WEBSITE with no domain/letters) is set entirely to
    ``O``. Conservative by construction: the violation test is byte-equivalent to
    the verifier's, so legitimate multi-token entities (e.g. "(805) 917-4200",
    valid currency totals) are never demoted and arithmetic/bbox checks are
    untouched (no token is dropped, no bbox is changed). Orphan ``I-`` tags are
    re-promoted to ``B-`` to keep valid BIO.
    """
    tags = list(ner_tags)
    for lab, i, j in _verifier_entity_runs(tags):
        if lab not in _VERIFIER_CONTENT_LABELS:
            continue
        text = " ".join((tokens[k] or "") for k in range(i, j + 1)).strip()
        if _entity_run_violates(lab, text):
            for k in range(i, j + 1):
                tags[k] = "O"

    # Re-emit valid BIO: any surviving ``I-`` whose predecessor is a different
    # label (or none) becomes a ``B-`` so there are no orphan ``I-`` tags.
    for k in range(len(tags)):
        if str(tags[k]).startswith("I-"):
            lab = _tag_label(tags[k])
            if k == 0 or _tag_label(tags[k - 1]) != lab:
                tags[k] = f"B-{lab}"
    return tokens, bboxes, tags


# --- header de-duplication ------------------------------------------------------
_STRAY_COMMA_RE = re.compile(r",\s*,+")
# Tiny stopword set so a generic merchant word like "THE" doesn't pick a stray run.
_MERCHANT_NAME_STOPWORDS = {"THE", "AND"}


def _merchant_name_words(merchant_name: str | None) -> set[str]:
    if not merchant_name:
        return set()
    return {
        raw.upper()
        for raw in re.split(r"[^A-Za-z0-9]+", str(merchant_name))
        if raw
    }


def _merchant_name_tokens(merchant_name: str | None) -> set[str]:
    """Significant uppercase tokens from a merchant name (for canonical-run match).

    Generalizes the Sprouts-specific ``"SPROUT" in text`` test: a header run names
    the merchant when its joined text contains any of these tokens.
    """
    if not merchant_name:
        return set()
    out: set[str] = set()
    for raw in re.split(r"[^A-Za-z]+", str(merchant_name)):
        word = raw.upper()
        if len(word) >= 3 and word not in _MERCHANT_NAME_STOPWORDS:
            out.add(word)
    return out


def _token_name_word(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(token or "")).upper()


def _promote_missing_merchant_header(
    tokens: list[str],
    bboxes: list[list[int]],
    tags: list[str],
    merchant_name: str | None,
) -> None:
    """Recover a missing MERCHANT_NAME span from the top matching header line.

    Some generic merchant candidates arrive with the visible wordmark/header
    tokens unlabeled (or as the non-core STORE_NAME alias). The verifier requires
    exactly one ``B-MERCHANT_NAME`` tag, so when no merchant span exists we use the
    caller-provided merchant name to tag the top visual line that actually names
    the merchant. Existing candidates that already have a merchant span are left
    to the normal duplicate-header pruning path.
    """
    name_tokens = _merchant_name_tokens(merchant_name)
    if not name_tokens:
        return

    all_name_words = _merchant_name_words(merchant_name)
    for line in _visual_lines(bboxes):
        ordered = sorted(line, key=lambda i: bboxes[i][0])
        line_text = " ".join(str(tokens[i]) for i in ordered).upper()
        if not any(token in line_text for token in name_tokens):
            continue

        selected = []
        for i in ordered:
            word = _token_name_word(str(tokens[i]))
            if not word:
                continue
            if (
                word in all_name_words
                or any(token in word or word in token for token in name_tokens)
            ):
                selected.append(i)
        if not selected:
            selected = ordered

        for offset, i in enumerate(selected):
            tags[i] = "B-MERCHANT_NAME" if offset == 0 else "I-MERCHANT_NAME"
        return


def _dedupe_header_blocks(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
    merchant_name: str | None = None,
) -> tuple[list[str], list[list[int]], list[str]]:
    """Guarantee a single MERCHANT_NAME (and ADDRESS_LINE) header block.

    Cloned base receipts can carry the merchant header twice, and the per-line BIO
    flatten emits a fresh ``B-`` on every visual line of a multi-line header. Both
    inflate the ``B-MERCHANT_NAME`` count. We keep one canonical block (the one
    that names the merchant, else the longest), drop the redundant rows, scrub
    stray-comma address artifacts, then re-emit BIO so each surviving header block
    has exactly one ``B-`` tag.
    """
    tokens = list(tokens)
    bboxes = list(bboxes)
    tags = list(ner_tags)
    drop: set[int] = set()

    if not _label_runs(tags, "MERCHANT_NAME"):
        # STORE_NAME is a renderer-level alias, but the objective verifier only
        # counts MERCHANT_NAME. Normalize it only for otherwise-missing headers
        # so already-passing candidates are not disturbed.
        for k, tag in enumerate(tags):
            if _tag_label(tag) == "STORE_NAME":
                tags[k] = (
                    "B-MERCHANT_NAME"
                    if str(tag).startswith("B-")
                    else "I-MERCHANT_NAME"
                )
        if not _label_runs(tags, "MERCHANT_NAME"):
            _promote_missing_merchant_header(tokens, bboxes, tags, merchant_name)

    # MERCHANT_NAME: when multiple non-contiguous blocks exist, keep the one that
    # actually names the merchant (else the longest) and drop the rest.
    merch_runs = _label_runs(tags, "MERCHANT_NAME")
    if len(merch_runs) > 1:
        def _run_text(run: tuple[int, int]) -> str:
            return " ".join(str(tokens[k]) for k in range(run[0], run[1])).upper()

        name_tokens = _merchant_name_tokens(merchant_name)
        canon = None
        if name_tokens:
            canon = next(
                (
                    run
                    for run in merch_runs
                    if any(tok in _run_text(run) for tok in name_tokens)
                ),
                None,
            )
        if canon is None:
            canon = max(merch_runs, key=lambda run: run[1] - run[0])
        for run in merch_runs:
            if run != canon:
                drop.update(range(run[0], run[1]))

    # ADDRESS_LINE: keep the first block, drop duplicates, scrub comma junk.
    addr_runs = _label_runs(tags, "ADDRESS_LINE")
    if addr_runs:
        keep_addr = addr_runs[0]
        for run in addr_runs[1:]:
            drop.update(range(run[0], run[1]))
        for k in range(keep_addr[0], keep_addr[1]):
            text = str(tokens[k])
            stripped = text.strip()
            if stripped in {"", ","}:
                # lone stray comma ("WESTLAKE , , CA" -> drop the bare comma)
                drop.add(k)
            elif _STRAY_COMMA_RE.search(text):
                tokens[k] = _STRAY_COMMA_RE.sub(",", text)

    if drop:
        keep = [i for i in range(len(tokens)) if i not in drop]
        tokens = [tokens[i] for i in keep]
        bboxes = [bboxes[i] for i in keep]
        tags = [tags[i] for i in keep]

    # Re-emit BIO within each surviving header block so a multi-line block carries
    # exactly one B- tag (the per-line flatten resets it every line).
    for label in ("MERCHANT_NAME", "ADDRESS_LINE"):
        for start, end in _label_runs(tags, label):
            for k in range(start, end):
                tags[k] = f"B-{label}" if k == start else f"I-{label}"

    return tokens, bboxes, tags


# --- payment-block de-duplication -----------------------------------------------
# Masked card token (matches the verifier's `[X*x]{4,}` collector).
_CARD_MASK_RE = re.compile(r"[X*x]{4,}")
# Tender/EMV vocabulary used to detect (and group) a payment block.
_PAYMENT_VOCAB = {
    "DEBIT", "CREDIT", "CARD", "CARD#:", "CARD#", "#:", "PURCHASE",
    "APPROVED", "AUTH", "CODE", "CODE:", "REF", "REF#", "REF#:", "ENTRY",
    "METHOD", "ISSUER", "MODE", "MODE:", "PIN", "VERIFIED", "AID", "AID:",
    "TVR", "TVR:", "IAD", "IAD:", "TSI", "TSI:", "ARC", "ARC:", "TC", "TC:",
    "MID", "MID:", "TID", "TID:", "SEQ", "SEQ:", "CONTACTLESS", "CHIP",
    "MASTERCARD", "VISA", "AMEX", "TENDER", "CHANGE", "APPROVAL", "TRACE",
}
# Labels we must never drop (totals/geometry) when pruning a payment block.
_PROTECTED_LABELS = {
    "LINE_TOTAL", "GRAND_TOTAL", "SUBTOTAL", "TAX", "UNIT_PRICE",
    "PRODUCT_NAME", "QUANTITY", "MERCHANT_NAME", "ADDRESS_LINE", "PHONE",
    "DATE", "TIME", "STORE_HOURS", "WEBSITE",
}


def _card_tail(token: str) -> str | None:
    """Trailing-4 the verifier would record for a masked-card token, or None."""
    if token and _CARD_MASK_RE.search(token):
        return token[-4:]
    return None


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token or "")


def _dedupe_payment_blocks(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> tuple[list[str], list[list[int]], list[str]]:
    """Guarantee at most one masked-card trailing-4 (one payment block).

    Cloned/augmented base receipts can carry two tender sections: a primary
    card block near the top (``CARD #: XXXX…8712 / PURCHASE APPROVED / AUTH
    CODE``) plus a second injected block lower (``DEBIT $X / CARD#: XXXX…674S /
    PURCHASE APPROVED AUTH CODE``). The verifier collects every ``[X*x]{4,}``
    token's trailing-4 and fails on >1 distinct tail, so the redundant block (or
    stray masked dividers such as ``****************``) reads as a fake tell.

    We pick a canonical tail (the earliest digit-bearing real card), drop any
    payment block whose card tail differs, and drop stray non-canonical masked
    tokens (dividers / OCR garble). Total/geometry rows are never dropped, so
    arithmetic and bbox validity are preserved.
    """
    tokens = list(tokens)
    bboxes = list(bboxes)
    tags = list(ner_tags)

    mask_idx = [i for i in range(len(tokens)) if _card_tail(str(tokens[i])) is not None]
    if not mask_idx:
        return tokens, bboxes, tags

    # Group token indices into top->bottom visual lines and classify payment lines.
    lines = _visual_lines(bboxes)  # already ordered top->bottom
    blocks: list[dict[str, Any]] = []  # consecutive runs of payment lines
    cur: list[int] | None = None

    def _line_is_payment(idxs: list[int]) -> bool:
        has_marker = False
        for i in idxs:
            if _tag_label(tags[i]) in _PROTECTED_LABELS:
                return False
            tok = str(tokens[i]).upper().strip()
            if _card_tail(tokens[i]) is not None or tok in _PAYMENT_VOCAB:
                has_marker = True
        return has_marker

    for idxs in lines:
        if _line_is_payment(idxs):
            if cur is None:
                cur = []
            cur.extend(idxs)
        else:
            if cur:
                blocks.append({"idxs": cur})
                cur = None
    if cur:
        blocks.append({"idxs": cur})

    # Tails present in each block (digit-bearing = a real card number).
    for blk in blocks:
        digit_tails: list[str] = []
        all_tails: list[str] = []
        for i in blk["idxs"]:
            tail = _card_tail(str(tokens[i]))
            if tail is None:
                continue
            all_tails.append(tail)
            if _has_digit(str(tokens[i])):
                digit_tails.append(tail)
        blk["digit_tails"] = digit_tails
        blk["all_tails"] = all_tails

    # Canonical tail: the earliest (top-most) block's real card number. Fall back
    # to the first masked token's tail when nothing carries digits.
    canonical: str | None = None
    for blk in blocks:
        if blk["digit_tails"]:
            canonical = blk["digit_tails"][0]
            break
    if canonical is None:
        canonical = _card_tail(str(tokens[mask_idx[0]]))

    drop: set[int] = set()

    # Drop whole redundant card blocks (a real card whose tail != canonical and
    # which does not also contain the canonical tail).
    for blk in blocks:
        tails = set(blk["all_tails"])
        if blk["digit_tails"] and canonical not in tails:
            drop.update(blk["idxs"])

    # Drop stray non-canonical masked tokens (dividers / garble) that survived
    # outside a dropped block, so no second trailing-4 leaks through.
    for i in mask_idx:
        if i in drop:
            continue
        if _card_tail(str(tokens[i])) != canonical:
            drop.add(i)

    if not drop:
        return tokens, bboxes, tags

    keep = [i for i in range(len(tokens)) if i not in drop]
    tokens = [tokens[i] for i in keep]
    bboxes = [bboxes[i] for i in keep]
    tags = [tags[i] for i in keep]

    # Re-emit BIO for any label run we may have truncated so each block keeps a
    # single leading B- tag.
    for label in ("PAYMENT_METHOD",):
        for start, end in _label_runs(tags, label):
            for k in range(start, end):
                tags[k] = f"B-{label}" if k == start else f"I-{label}"
    return tokens, bboxes, tags


# --- visual-line de-overlap / re-spacing ----------------------------------------
def _visual_lines(bboxes: list[list[int]]) -> list[list[int]]:
    """Group word indices into visual lines by y-center proximity.

    Mirrors the verifier's line grouping so the de-overlap pass operates on the
    same lines that no_overlap is scored against.
    """
    centers = [
        ((b[1] + b[3]) / 2, i)
        for i, b in enumerate(bboxes)
        if isinstance(b, (list, tuple)) and len(b) == 4
    ]
    centers.sort(reverse=True)
    lines: list[list[int]] = []
    cur: list[int] = []
    last: float | None = None
    for cy, i in centers:
        if last is None or abs(cy - last) <= 8:
            cur.append(i)
        else:
            lines.append(cur)
            cur = [i]
        last = cy
    if cur:
        lines.append(cur)
    return lines


def _deoverlap_line_words(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> tuple[list[str], list[list[int]], list[str]]:
    """De-overlap AND re-space words sharing a visual line (footer squish).

    Two-pass per visual line:

    1. De-overlap: sweep left-to-right; when a word starts left of the previous
       word's right edge (beyond the verifier's 2px tolerance) we either drop it
       (stacked unlabeled ``O`` junk such as a copied footer block) or nudge it
       right to clear the overlap.
    2. Re-space: removing overlaps leaves words *touching* (gap ~0), which the
       verifier's ``word_spacing`` check flags. We then walk each line and, where
       the gap is below a normal word-space, nudge the later word (and every word
       after it on the line, via a running shift) right to open a real gap.

    Both passes preserve right-aligned price columns (existing large gaps are
    never shrunk, because the running shift moves later words by the same delta)
    and stay clamped in ``[0, 1000]`` without re-introducing overlaps.
    """
    boxes = [list(b) if isinstance(b, (list, tuple)) else b for b in bboxes]
    drop: set[int] = set()

    # Pass 1: remove overlaps.
    for line in _visual_lines(boxes):
        ordered = sorted(line, key=lambda i: boxes[i][0])
        prev_right: float | None = None
        for i in ordered:
            x0, y0, x1, y1 = boxes[i]
            if prev_right is not None and x0 < prev_right - 2:
                if _tag_label(ner_tags[i]) == "O":
                    # stacked, unlabeled footer/EMV duplicate -> drop it
                    drop.add(i)
                    continue
                # labeled content -> nudge right to clear the overlap
                shift = (prev_right + 1) - x0
                x0 += shift
                x1 += shift
                if x1 > 1000:
                    x1 = 1000
                    if x0 >= x1:
                        x0 = x1 - 1
                boxes[i] = [x0, y0, x1, y1]
            prev_right = boxes[i][2]

    if drop:
        keep = [i for i in range(len(tokens)) if i not in drop]
        tokens = [tokens[i] for i in keep]
        boxes = [boxes[i] for i in keep]
        ner_tags = [ner_tags[i] for i in keep]

    # Pass 2: open a real word-gap on every line.
    for line in _visual_lines(boxes):
        _respace_visual_line(boxes, line)

    return tokens, boxes, ner_tags


def _respace_visual_line(boxes: list[list[int]], line: list[int]) -> None:
    """Open a real word-gap between adjacent words on a single visual line.

    Target gap is ``~0.40 * line_height`` (with a small absolute floor), which
    clears the verifier's ``0.30 * line_height`` threshold with margin even after
    integer rounding. A monotone running shift is applied left->right so existing
    large gaps (the right-aligned price column) are carried along, never shrunk.
    The whole line is shifted back left to fit within ``[0, 1000]``; if it cannot
    fit, the line is left unchanged so we never violate bbox/overlap checks.
    """
    if len(line) < 2:
        return
    ordered = sorted(line, key=lambda i: boxes[i][0])
    orig = [list(boxes[i]) for i in ordered]
    new: list[list[float]] = []
    running = 0.0
    prev_right: float | None = None
    for k, b in enumerate(orig):
        x0 = b[0] + running
        x1 = b[2] + running
        if prev_right is not None:
            h = max(orig[k - 1][3] - orig[k - 1][1], 6)
            target = max(0.40 * h, 3.0)
            gap = x0 - prev_right
            if gap < target:
                extra = target - gap
                running += extra
                x0 += extra
                x1 += extra
        new.append([x0, x1])
        prev_right = x1

    # Shift the whole (now wider) line left to fit within bounds, preserving gaps.
    max_x1 = max(p[1] for p in new)
    min_x0 = min(p[0] for p in new)
    if max_x1 > 1000:
        shift_left = min(max_x1 - 1000, min_x0)
        for p in new:
            p[0] -= shift_left
            p[1] -= shift_left
        max_x1 -= shift_left
        min_x0 -= shift_left

    if max_x1 <= 1000 and min_x0 >= 0:
        for idx, i in enumerate(ordered):
            boxes[i][0] = int(round(new[idx][0]))
            boxes[i][2] = int(round(new[idx][1]))
        return

    # If the line is already hard against both page edges, pushing words right
    # cannot open verifier-safe gaps. Compress only this visual line enough to
    # keep the same order, preserve positive-width boxes, and fit target gaps.
    widths = [max(float(b[2] - b[0]), 1.0) for b in orig]
    gaps = [
        max(0.40 * max(orig[k][3] - orig[k][1], 6), 3.0)
        for k in range(len(orig) - 1)
    ]

    left_anchor = max(0.0, min(float(b[0]) for b in orig))
    available = 1000.0 - left_anchor
    required_min = float(len(orig)) + sum(gaps)
    if available < required_min:
        left_anchor = 0.0
        available = 1000.0
    if available < required_min:
        return

    scale = min(1.0, (available - sum(gaps)) / sum(widths))
    if scale <= 0:
        return

    compressed: list[list[float]] = []
    x = left_anchor
    for k, width in enumerate(widths):
        scaled_width = max(width * scale, 1.0)
        compressed.append([x, x + scaled_width])
        x += scaled_width
        if k < len(gaps):
            x += gaps[k]

    if compressed[-1][1] > 1000 or compressed[0][0] < 0:
        return
    for idx, i in enumerate(ordered):
        boxes[i][0] = int(round(compressed[idx][0]))
        boxes[i][2] = int(round(compressed[idx][1]))


# --- canonical entry point ------------------------------------------------------
def reconcile_candidate(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
    merchant_name: str | None = None,
) -> tuple[list[str], list[list[int]], list[str]]:
    """Run every reconciliation pass in the canonical order.

    Cleans a flattened synthetic candidate so it survives objective verification:
      1. collapse/drop duplicate header blocks -> exactly one MERCHANT_NAME (and
         one ADDRESS_LINE block), and strip stray-comma address artifacts.
      2. collapse/drop duplicate payment/card blocks -> at most one masked-card
         trailing-4 (the injected second tender block tell).
      3. de-overlap AND re-space words that share a visual line so adjacent words
         carry a real word-gap (the footer "squish" tell).
      4. content-aware label sanitization: demote-to-O any BIO entity run whose
         joined text can't be its label (mislabeled entities poison LayoutLM).

    Returns the cleaned ``(tokens, bboxes, ner_tags)``.
    """
    tokens, bboxes, ner_tags = clean_token_text(
        tokens, bboxes, ner_tags, merchant_name
    )
    tokens, bboxes, ner_tags = _dedupe_header_blocks(
        tokens, bboxes, ner_tags, merchant_name
    )
    tokens, bboxes, ner_tags = _dedupe_payment_blocks(tokens, bboxes, ner_tags)
    tokens, bboxes, ner_tags = _deoverlap_line_words(tokens, bboxes, ner_tags)
    tokens, bboxes, ner_tags = _sanitize_entity_labels(tokens, bboxes, ner_tags)
    return tokens, bboxes, ner_tags

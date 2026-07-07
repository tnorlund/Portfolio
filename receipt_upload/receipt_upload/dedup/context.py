"""Deterministic merge-context ("dossier") builder for receipt de-duplication.

Stage 1 of the merge pipeline:

    group by raw-pixel sha256  ->  MergeDossier  ->  resolver (stage 2)

A ``MergeDossier`` names the members of one duplicate group and the chosen
**survivor** (the highest-label-quality copy). The pass is pure (no I/O) and
deterministic, so it is cheap, reproducible, and testable.

Duplicates are anchored on the **receipt-level sha256** of the raw receipt
pixels, which is provably reliable (100% precision in verification). Receipts
that share a sha256 are byte-identical crops of the same receipt, whether they
live on the *same* parent image (segmenter mis-split) or *different* images
(re-uploaded photo / re-scanned receipt). The whole-photo *image* sha256 is
deliberately NOT used as an anchor: early-dev uploads reused non-unique raw
keys, so image hashes collide across unrelated merchants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.constants import CORE_LABELS

from receipt_upload.dedup.detector import group_by_pixels
from receipt_upload.label_validation.label_normalization import (
    NON_CORE_LABEL_ALIASES,
    canonical_label_name,
)

Key = Tuple[str, int]  # (image_id, receipt_id)
CANONICAL_LABELS = set(CORE_LABELS)

# Safe legacy/model aliases -> canonical (extends the shared production map
# with the older taxonomy seen in historical dev labels). Only mappings with a
# single unambiguous canonical target belong here.
_SAFE_ALIASES: Dict[str, str] = {
    **NON_CORE_LABEL_ALIASES,  # ADDRESS, BUSINESS_NAME, CARD_NUMBER, etc.
    "PHONE": "PHONE_NUMBER",
    "ITEM_NAME": "PRODUCT_NAME",
    "ITEM_DESCRIPTION": "PRODUCT_NAME",
    "ITEM_QUANTITY": "QUANTITY",
    "ITEM_TOTAL": "LINE_TOTAL",
    "TENDER": "PAYMENT_METHOD",
}
# Meaningful but AMBIGUOUS legacy labels — a word genuinely carries this
# concept but it can map to >1 canonical label, so it is NOT auto-mapped.
# ITEM_PRICE is ambiguous: an "item price" can be a per-unit UNIT_PRICE or the
# extended LINE_TOTAL shown on the line.
_AMBIGUOUS_LEGACY = {"AMOUNT", "TOTAL", "ITEM", "ITEM_PRICE"}


def is_valid_status(status) -> bool:
    """True iff a validation status reads as VALID (and not INVALID)."""
    s = str(status or "").upper()
    return "VALID" in s and "INVALID" not in s


def resolve_label(label) -> Tuple[Optional[str], str]:
    """Classify a stored label.

    Returns ``(canonical_or_None, kind)`` where kind is:
      * ``"canonical"`` — the CORE label to use (direct or safe alias)
      * ``"legacy"`` — ambiguous; ``canonical`` is None, resolved elsewhere
      * ``"junk"`` — not a real label (raw value / instruction note / OTHER)
    """
    up = canonical_label_name(label)
    if up in CANONICAL_LABELS:
        return up, "canonical"
    if up in _SAFE_ALIASES:
        return _SAFE_ALIASES[up], "canonical"
    if up in _AMBIGUOUS_LEGACY:
        return None, "legacy"
    return None, "junk"


@dataclass
class LabelObs:
    """One label as observed on a receipt word.

    ``label`` is the original stored value; ``canonical`` / ``kind`` are the
    normalized classification (see :func:`resolve_label`).
    """

    label: str
    line_id: int
    word_id: int
    word_text: str
    validation_status: Optional[str] = None

    def __post_init__(self) -> None:
        self.canonical, self.kind = resolve_label(self.label)

    @property
    def pos(self) -> str:
        return f"{self.line_id}:{self.word_id}"

    @property
    def is_canonical(self) -> bool:
        return self.kind == "canonical"

    @property
    def is_legacy(self) -> bool:
        return self.kind == "legacy"

    @property
    def is_junk(self) -> bool:
        return self.kind == "junk"

    @property
    def effective_label(self) -> Optional[str]:
        """The canonical label to use (None for legacy/junk)."""
        return self.canonical

    @property
    def is_validated(self) -> bool:
        return is_valid_status(self.validation_status)


@dataclass
class MemberContext:
    """One receipt in a duplicate group, summarized for the resolver."""

    image_id: str
    receipt_id: int
    width: int
    height: int
    resolution: int
    n_words: int
    n_canonical_labels: int
    n_validated: int
    labels: List[Dict] = field(default_factory=list)  # per-word observations
    # full (UNtruncated) word_text -> ["line:word", ...]; for gap-fill
    # targeting
    word_index: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def key(self) -> Key:
        return (self.image_id, self.receipt_id)

    @property
    def key_str(self) -> str:
        return f"{self.image_id}#{self.receipt_id}"


@dataclass
class MergeDossier:
    """The members of one duplicate group and the chosen survivor."""

    group_id: str
    anchor: str  # "receipt_sha256" | "transaction_identity"
    scope: str  # "within_image" | "cross_image"
    sha256: str
    members: List[MemberContext]
    survivor_suggested: str
    notes: List[str] = field(default_factory=list)


def _survivor_score(m: MemberContext) -> Tuple:
    # Quality first (validated > canonical-label count > OCR coverage), pixels
    # last, then a deterministic tie-break. A higher tuple wins.
    return (
        m.n_validated,
        m.n_canonical_labels,
        m.n_words,
        m.resolution,
        m.image_id,
        m.receipt_id,
    )


def _valid_pairs(m: MemberContext) -> set:
    """VALID canonical ``(word_text, label)`` pairs carried by one member."""
    return {
        (o["word_text"], o["canonical_label"])
        for o in m.labels
        if o["canonical"] and is_valid_status(o["validation_status"])
    }


def _pick_survivor(members: List[MemberContext]) -> MemberContext:
    """Choose the survivor that RETAINS the most VALID labels after merge.

    A dropped copy's VALID label can only migrate onto a survivor whose OCR
    already produced that word (text). Cross-image re-uploads often have
    divergent OCR, so picking purely by label count can keep a copy whose words
    can't hold the others' labels — stranding them (and sometimes keeping the
    worse-OCR copy). We instead maximize the union of VALID ``(text, label)``
    pairs the candidate can hold: its own plus every other member's pair whose
    text the candidate also has. Ties fall back to label quality then OCR
    coverage (:func:`_survivor_score`).
    """
    pairs = [_valid_pairs(m) for m in members]
    words = [set(m.word_index) for m in members]

    def retained(i: int) -> int:
        keep = set(pairs[i])
        for j, pj in enumerate(pairs):
            if j != i:
                keep |= {(t, lab) for (t, lab) in pj if t in words[i]}
        return len(keep)

    best = max(
        range(len(members)),
        key=lambda i: (retained(i), _survivor_score(members[i])),
    )
    return members[best]


def _build_member(r, words: Dict[Tuple[int, int], str], obs) -> MemberContext:
    # full word index (NOT truncated) for membership + unique-target gap-fills
    word_index: Dict[str, List[str]] = {}
    for (ln, wd), t in words.items():
        if t:
            word_index.setdefault(t, []).append(f"{ln}:{wd}")
    canonical = [o for o in obs if o.is_canonical]
    return MemberContext(
        image_id=r.image_id,
        receipt_id=r.receipt_id,
        width=r.width,
        height=r.height,
        resolution=(r.width or 0) * (r.height or 0),
        n_words=len(words),
        n_canonical_labels=len(canonical),
        n_validated=sum(1 for o in obs if o.is_validated),
        word_index=word_index,
        labels=[
            {
                "pos": o.pos,
                "word_text": o.word_text,
                "label": o.label,  # original stored value
                "canonical_label": o.effective_label,  # CORE label or None
                "kind": o.kind,  # canonical | legacy | junk
                "validation_status": o.validation_status,
                "canonical": o.is_canonical,
            }
            for o in obs
        ],
    )


def build_merge_dossiers(
    receipts: List,
    words_by_receipt: Dict[Key, Dict[Tuple[int, int], str]],
    labels_by_receipt: Dict[Key, List[LabelObs]],
) -> List[MergeDossier]:
    """Group receipts by raw-pixel ``sha256`` and build one dossier per group.

    Parameters
    ----------
    receipts
        Receipt entities (need ``image_id``, ``receipt_id``, ``sha256``,
        ``width``, ``height``).
    words_by_receipt
        ``{(image_id, receipt_id): {(line_id, word_id): text}}``.
    labels_by_receipt
        ``{(image_id, receipt_id): [LabelObs, ...]}``.
    """
    dossiers: List[MergeDossier] = []
    for sha, recs in group_by_pixels(receipts):
        d = _assemble_dossier(
            recs,
            words_by_receipt,
            labels_by_receipt,
            anchor="receipt_sha256",
            sha256=sha,
        )
        if d is not None:
            dossiers.append(d)

    dossiers.sort(key=lambda d: (d.scope, -len(d.members), d.sha256))
    return dossiers


def _assemble_dossier(
    recs: List,
    words_by_receipt: Dict[Key, Dict[Tuple[int, int], str]],
    labels_by_receipt: Dict[Key, List[LabelObs]],
    *,
    anchor: str,
    sha256: str = "",
    group_id: Optional[str] = None,
) -> Optional[MergeDossier]:
    """Build one dossier from a set of receipt entities (any grouping)."""
    image_ids = {r.image_id for r in recs}
    members = [
        _build_member(
            r,
            words_by_receipt.get((r.image_id, r.receipt_id), {}),
            labels_by_receipt.get((r.image_id, r.receipt_id), []),
        )
        for r in recs
    ]
    seen, uniq = set(), []
    for m in members:
        if m.key not in seen:
            seen.add(m.key)
            uniq.append(m)
    members = uniq
    if len(members) < 2:
        return None

    scope = "within_image" if len(image_ids) == 1 else "cross_image"
    survivor = _pick_survivor(members)

    notes: List[str] = []
    if scope == "within_image" and anchor == "receipt_sha256":
        notes.append(
            f"Segmenter mis-split parent image {sorted(image_ids)[0]} into "
            f"{len(members)} byte-identical crops of one receipt."
        )
    if survivor.n_canonical_labels == 0:
        notes.append(
            "Suggested survivor has 0 canonical labels — labels live on a "
            "non-survivor; consolidation is mandatory."
        )

    gid = group_id or f"{(sha256 or members[0].image_id)[:12]}_{scope}"
    return MergeDossier(
        group_id=gid,
        anchor=anchor,
        scope=scope,
        sha256=sha256,
        members=members,
        survivor_suggested=survivor.key_str,
        notes=notes,
    )


def build_dossiers_for_groups(
    groups: List[List[Key]],
    receipts_by_key: Dict[Key, object],
    words_by_receipt: Dict[Key, Dict[Tuple[int, int], str]],
    labels_by_receipt: Dict[Key, List[LabelObs]],
    *,
    anchor: str = "transaction_identity",
) -> List[MergeDossier]:
    """Build dossiers from EXPLICIT receipt groups (e.g. confirmed cross-image
    near-duplicates) rather than sha256-grouping. Survivor is chosen by label
    quality as for the byte-identical path; gap-fills are text-based."""
    out: List[MergeDossier] = []
    for g in groups:
        recs = [receipts_by_key[k] for k in g if k in receipts_by_key]
        if len(recs) < 2:
            continue
        d = _assemble_dossier(
            recs,
            words_by_receipt,
            labels_by_receipt,
            anchor=anchor,
            group_id=f"{recs[0].image_id[:8]}_near",
        )
        if d is not None:
            out.append(d)
    return out

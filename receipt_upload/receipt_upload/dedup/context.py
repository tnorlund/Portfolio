"""Deterministic merge-context ("dossier") builder for receipt de-duplication.

This is **stage 1** of a two-stage merge pipeline:

    deterministic pass (this module)  ->  MergeDossier (JSON)  ->  LLM resolver

A ``MergeDossier`` is the *necessary context* an LLM needs to safely merge a set
of duplicate receipts: the members, the consolidated label union, the explicit
label conflicts to resolve, a survivor ranking by label quality, and any
non-canonical "junk" labels to strip. The pass is pure (no I/O) and
deterministic so it's cheap, reproducible, and testable.

Key design decision (see dedup analysis): duplicates are anchored on the
**receipt-level sha256** of the raw receipt pixels, which is provably reliable
(100% precision in verification). Receipts that share a sha256 are
byte-identical
crops of the same physical receipt, whether they live on the *same* parent image
(segmenter mis-split) or *different* images (re-uploaded photo / re-scanned
receipt). The whole-photo *image* sha256 is deliberately NOT used as an anchor:
early-dev uploads reused non-unique raw keys (``raw-receipts/receipt.png``), so
image hashes collide across unrelated merchants. Real image re-uploads are
still caught here because their receipt crops are byte-identical.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import re

from receipt_dynamo.constants import CORE_LABELS
from receipt_upload.label_validation.label_normalization import (
    NON_CORE_LABEL_ALIASES,
    canonical_label_name,
)

Key = Tuple[str, int]  # (image_id, receipt_id)
CANONICAL_LABELS = set(CORE_LABELS)

_TEXT_LIMIT = 1500

# Safe legacy/model aliases -> canonical (extends the shared production map
# with the older taxonomy seen in historical dev labels). Only mappings with a
# single unambiguous canonical target belong here.
_SAFE_ALIASES: Dict[str, str] = {
    **NON_CORE_LABEL_ALIASES,  # ADDRESS, BUSINESS_NAME, CARD_NUMBER, PAYMENT_TYPE
    "PHONE": "PHONE_NUMBER",
    "ITEM_NAME": "PRODUCT_NAME",
    "ITEM_DESCRIPTION": "PRODUCT_NAME",
    "ITEM_QUANTITY": "QUANTITY",
    "ITEM_TOTAL": "LINE_TOTAL",
    "TENDER": "PAYMENT_METHOD",
}
# Meaningful but AMBIGUOUS legacy labels — a word genuinely carries this
# concept but it can map to >1 canonical label, so it is NOT auto-mapped (left
# for an optional label-quality pass). ITEM_PRICE is ambiguous: an "item price"
# can be a per-unit UNIT_PRICE or the extended LINE_TOTAL shown on the line.
_AMBIGUOUS_LEGACY = {"AMOUNT", "TOTAL", "ITEM", "ITEM_PRICE"}
# Looks like a raw value rather than a label name (e.g. "4453.62", "$3.266EA").
_NUMBERISH = re.compile(
    r"^[\$\d.,()%/x\- ]*\d[\$\d.,()%/x\- ]*$", re.IGNORECASE
)


def resolve_label(label) -> Tuple[Optional[str], str]:
    """Classify a stored label.

    Returns ``(canonical_or_None, kind)`` where kind is:
      * ``"canonical"`` — ``canonical`` is the CORE label to use (direct or safe alias)
      * ``"legacy"``    — meaningful but ambiguous; ``canonical`` is None, LLM maps it
      * ``"junk"``      — not a real label (raw value / instruction note / OTHER)
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
        return (
            bool(self.validation_status)
            and "VALID" in str(self.validation_status).upper()
            and "INVALID" not in str(self.validation_status).upper()
        )


@dataclass
class MemberContext:
    """One receipt in a duplicate group, summarized for the LLM."""

    image_id: str
    receipt_id: int
    sha256: Optional[str]
    width: int
    height: int
    resolution: int
    n_words: int
    n_labels: int
    n_canonical_labels: int
    n_validated: int
    text: str
    label_counts: Dict[str, int]
    junk_labels: List[str]
    same_image_as_group: bool
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
class Conflict:
    """A locus where members disagree on the label (must be resolved on merge)."""

    locus: str  # "line:word" for pixel-aligned groups, else the word text
    word_text: str
    options: List[Dict]  # [{member, label, validation_status}]


@dataclass
class MergeDossier:
    """Everything an LLM needs to safely merge one duplicate group."""

    group_id: str
    anchor: str  # "receipt_sha256"
    scope: str  # "within_image" | "cross_image"
    trust: str  # "auto" | "guarded" | "manual"
    sha256: str
    members: List[MemberContext]
    label_union: Dict[str, int]  # canonical label -> #members carrying it
    conflicts: List[Conflict]
    labels_only_on_nonsurvivor: List[Dict]  # would be LOST on naive delete
    junk_flags: List[Dict]  # non-canonical "labels" to strip
    survivor_ranking: List[Dict]  # best-first [{key, score, reasons}]
    survivor_suggested: str
    label_overlap_pct: Optional[float]
    deterministic_action: str
    notes: List[str] = field(default_factory=list)

    def to_llm_context(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# survivor scoring
# --------------------------------------------------------------------------- #
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


def _rank_survivors(members: List[MemberContext]) -> List[Dict]:
    ranked = sorted(members, key=_survivor_score, reverse=True)
    out = []
    for m in ranked:
        reasons = []
        if m.n_validated:
            reasons.append(f"{m.n_validated} validated labels")
        reasons.append(f"{m.n_canonical_labels} canonical labels")
        reasons.append(f"{m.n_words} words")
        reasons.append(f"{m.resolution} px")
        out.append({"key": m.key_str, "reasons": reasons})
    return out


# --------------------------------------------------------------------------- #
# label union / conflicts / overlap
# --------------------------------------------------------------------------- #
def _candidate(o: LabelObs) -> Optional[str]:
    """The label a conflict should compare on: canonical, else the legacy name.

    Junk observations contribute no candidate (they're stripped, not resolved).
    """
    if o.is_canonical:
        return o.effective_label
    if o.is_legacy:
        return o.label  # original ambiguous name, e.g. AMOUNT — LLM maps it
    return None


def _label_union(
    label_obs: Dict[Key, List[LabelObs]], members
) -> Dict[str, int]:
    """Canonical label -> number of DISTINCT members carrying it."""
    counts: Dict[str, set] = {}
    for m in members:
        seen = {
            o.effective_label
            for o in label_obs.get(m.key, [])
            if o.is_canonical
        }
        for lab in seen:
            counts.setdefault(lab, set()).add(m.key)
    return {lab: len(ks) for lab, ks in sorted(counts.items())}


def _conflicts_by(
    label_obs: Dict[Key, List[LabelObs]], members, key_of
) -> List[Conflict]:
    """Generic conflict finder: group observations by ``key_of(obs)`` and flag
    loci where >1 distinct candidate label (canonical or legacy) appears."""
    at: Dict[str, Dict[Key, LabelObs]] = {}
    text_at: Dict[str, str] = {}
    for m in members:
        for o in label_obs.get(m.key, []):
            if _candidate(o) is None:  # skip junk
                continue
            k = key_of(o)
            if k is None:
                continue
            at.setdefault(k, {})[m.key] = o
            text_at.setdefault(k, o.word_text)
    conflicts = []
    for locus, by_member in at.items():
        cands = {_candidate(o) for o in by_member.values()}
        if len(cands) > 1 and len(by_member) > 1:
            conflicts.append(
                Conflict(
                    locus=locus,
                    word_text=text_at.get(locus, ""),
                    options=[
                        {
                            "member": f"{k[0]}#{k[1]}",
                            "label": _candidate(o),
                            "kind": o.kind,
                            "validation_status": o.validation_status,
                        }
                        for k, o in by_member.items()
                    ],
                )
            )
    return conflicts


def _positional_conflicts(label_obs, members) -> List[Conflict]:
    """Same (line:word) labeled differently across pixel-identical members."""
    return _conflicts_by(label_obs, members, key_of=lambda o: o.pos)


def _text_conflicts(label_obs, members) -> List[Conflict]:
    """For cross-OCR (different images): same word TEXT labeled differently."""
    return _conflicts_by(
        label_obs, members, key_of=lambda o: o.word_text or None
    )


def _label_key(o: LabelObs, scope: str):
    """Identity used to compare labels across copies.

    within_image groups are PIXEL-aligned, so two equal tokens at different
    positions are distinct slots — key on (pos, label). cross_image groups have
    independent OCR, so positions don't correspond — key on (word_text, label).
    """
    if scope == "within_image":
        return (o.pos, o.effective_label)
    return (o.word_text, o.effective_label)


def _text_overlap_pct(
    label_obs: Dict[Key, List[LabelObs]],
    members,
    survivor: MemberContext,
    scope: str,
) -> Optional[float]:
    """Label overlap between survivor and the rest (position-aware within-image)."""

    def pairs(m):
        return {
            _label_key(o, scope)
            for o in label_obs.get(m.key, [])
            if o.is_canonical
        }

    sp = pairs(survivor)
    union = set(sp)
    only_other = set()
    for m in members:
        if m.key == survivor.key:
            continue
        p = pairs(m)
        union |= p
        only_other |= p - sp
    if not union:
        return None
    return round(100 * (len(union) - len(only_other)) / len(union), 1)


def _labels_only_on_nonsurvivor(
    label_obs: Dict[Key, List[LabelObs]],
    members,
    survivor: MemberContext,
    scope: str,
) -> List[Dict]:
    surv_pairs = {
        _label_key(o, scope)
        for o in label_obs.get(survivor.key, [])
        if o.is_canonical
    }
    out = []
    for m in members:
        if m.key == survivor.key:
            continue
        for o in label_obs.get(m.key, []):
            if o.is_canonical and _label_key(o, scope) not in surv_pairs:
                out.append(
                    {
                        "member": m.key_str,
                        "label": o.effective_label,
                        "word_text": o.word_text,
                        "pos": o.pos,
                        "validation_status": o.validation_status,
                    }
                )
    return out


# --------------------------------------------------------------------------- #
# member construction
# --------------------------------------------------------------------------- #
def _build_member(
    r,
    words: Dict[Tuple[int, int], str],
    obs: List[LabelObs],
    group_image_id: str,
) -> MemberContext:
    text = " ".join(t for _, t in sorted(words.items()))
    # full word index (NOT truncated) for membership + unique-target gap-fills
    word_index: Dict[str, List[str]] = {}
    for (ln, wd), t in words.items():
        if t:
            word_index.setdefault(t, []).append(f"{ln}:{wd}")
    canonical = [o for o in obs if o.is_canonical]
    junk = sorted({o.label for o in obs if o.is_junk})
    counts: Dict[str, int] = {}
    for o in canonical:
        counts[o.effective_label] = counts.get(o.effective_label, 0) + 1
    return MemberContext(
        image_id=r.image_id,
        receipt_id=r.receipt_id,
        sha256=getattr(r, "sha256", None),
        width=r.width,
        height=r.height,
        resolution=(r.width or 0) * (r.height or 0),
        n_words=len(words),
        n_labels=len(obs),
        n_canonical_labels=len(canonical),
        n_validated=sum(1 for o in obs if o.is_validated),
        text=text[:_TEXT_LIMIT],
        label_counts=dict(sorted(counts.items())),
        junk_labels=junk,
        same_image_as_group=(r.image_id == group_image_id),
        word_index=word_index,
        labels=[
            {
                "pos": o.pos,
                "word_text": o.word_text,
                "label": o.label,  # original stored value
                "canonical_label": o.effective_label,  # normalized CORE label or None
                "kind": o.kind,  # canonical | legacy | junk
                "validation_status": o.validation_status,
                "canonical": o.is_canonical,
            }
            for o in obs
        ],
    )


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
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
    # Key on (sha256, width, height): identical raw-pixel bytes only prove a
    # true duplicate when the dimensions match too — same tobytes() across
    # different dims can collide for blank/uniform failed crops (sha hashes
    # pixels only).
    by_sha: Dict[Tuple, List] = {}
    for r in receipts:
        sha = getattr(r, "sha256", None)
        if sha:
            by_sha.setdefault((sha, r.width, r.height), []).append(r)

    dossiers: List[MergeDossier] = []
    for (sha, _w, _h), recs in by_sha.items():
        keys = {(r.image_id, r.receipt_id) for r in recs}
        if len(keys) < 2:
            continue  # not a duplicate
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
    """Build one dossier from a set of receipt entities (any grouping criterion)."""
    image_ids = {r.image_id for r in recs}
    group_image_id = sorted(image_ids)[0]
    members = [
        _build_member(
            r,
            words_by_receipt.get((r.image_id, r.receipt_id), {}),
            labels_by_receipt.get((r.image_id, r.receipt_id), []),
            group_image_id,
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
    ranking = _rank_survivors(members)
    survivor_key = ranking[0]["key"]
    survivor = next(m for m in members if m.key_str == survivor_key)

    if scope == "within_image":
        conflicts = _positional_conflicts(labels_by_receipt, members)
    else:
        conflicts = _text_conflicts(labels_by_receipt, members)

    union = _label_union(labels_by_receipt, members)
    only_non = _labels_only_on_nonsurvivor(
        labels_by_receipt, members, survivor, scope
    )
    overlap = _text_overlap_pct(labels_by_receipt, members, survivor, scope)
    junk = [
        {"member": m.key_str, "junk_labels": m.junk_labels}
        for m in members
        if m.junk_labels
    ]

    notes: List[str] = []
    if scope == "within_image" and anchor == "receipt_sha256":
        notes.append(
            f"Segmenter mis-split parent image {group_image_id} into "
            f"{len(members)} byte-identical crops of one receipt."
        )
    clean = not conflicts and not only_non and not junk
    action = "drop_redundant" if clean else "consolidate_then_drop"
    if any(m.junk_labels for m in members):
        notes.append("Non-canonical 'junk' labels present; strip on merge.")
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
        trust="auto" if anchor == "receipt_sha256" else "confirmed",
        sha256=sha256,
        members=members,
        label_union=union,
        conflicts=conflicts,
        labels_only_on_nonsurvivor=only_non,
        junk_flags=junk,
        survivor_ranking=ranking,
        survivor_suggested=survivor_key,
        label_overlap_pct=overlap,
        deterministic_action=action,
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
    quality exactly as for the byte-identical path; gap-fills are text-based."""
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

"""Stage 2 — deterministic duplicate MERGE plan (survivor + VALID gap-fills).

Merging duplicates is a dedup problem, not a label-adjudication problem. We pick
the highest-label-quality copy as the **survivor** and trust its labels. The only
thing the inferior copies can add is a label on a word the survivor *doesn't
label at all* — a **gap-fill**. We only ever copy a ``VALID`` label into an empty
slot, so:

  * the survivor's own labels are never overridden,
  * an ``INVALID`` (deliberately rejected) label is never resurrected,
  * there are no conflicts to adjudicate and no LLM is needed.

Re-adjudicating words where copies *disagree* is a separate, optional
label-quality pass — deliberately NOT part of the merge.

This module mutates nothing; it emits a reviewable ``MergeResolution`` plan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List

from receipt_upload.dedup.context import MergeDossier


def _is_valid(status) -> bool:
    s = str(status or "").upper()
    return "VALID" in s and "INVALID" not in s


@dataclass
class GapFill:
    locus: str  # "line:word" (within_image) or word_text (cross_image)
    word_text: str
    label: str
    from_member: str


@dataclass
class MergeResolution:
    group_id: str
    scope: str
    survivor: str
    survivor_label_count: int
    receipts_to_drop: List[str]
    gap_fills: List[GapFill]
    skipped_gap_disagreements: List[dict]  # dropped copies VALID-disagree on a gap word
    action: str  # drop_redundant | consolidate_then_drop
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_dossier(d: MergeDossier) -> MergeResolution:
    members = {m.key_str: m for m in d.members}
    survivor = d.survivor_suggested
    sm = members[survivor]
    drop = sorted(k for k in members if k != survivor)

    def locus_of(o: dict) -> str:
        return o["pos"] if d.scope == "within_image" else o["word_text"]

    # words the survivor already labels canonically -> survivor wins there
    survivor_labeled = {
        locus_of(o) for o in sm.labels if o.get("kind") == "canonical"
    }
    # words the survivor actually contains (for cross-image membership check;
    # within-image copies are byte-identical so any dropped locus exists here too)
    survivor_words = set(sm.text.split())

    # collect VALID canonical labels from dropped copies on survivor gap words
    candidates: Dict[str, List[dict]] = {}
    for k in drop:
        for o in members[k].labels:
            if o.get("kind") != "canonical" or not _is_valid(o.get("validation_status")):
                continue
            locus = locus_of(o)
            if locus in survivor_labeled:
                continue  # survivor already labels this word -> keep survivor's
            if d.scope == "cross_image" and o["word_text"] not in survivor_words:
                continue  # survivor doesn't even have this word
            candidates.setdefault(locus, []).append(
                {"label": o["canonical_label"], "from": k, "word_text": o["word_text"]}
            )

    gap_fills: List[GapFill] = []
    skipped: List[dict] = []
    for locus, cs in candidates.items():
        labels = {c["label"] for c in cs}
        if len(labels) == 1:
            c = cs[0]
            gap_fills.append(
                GapFill(locus=locus, word_text=c["word_text"], label=c["label"], from_member=c["from"])
            )
        else:
            # dropped copies VALID-disagree on a survivor gap word: don't guess.
            skipped.append(
                {
                    "locus": locus,
                    "word_text": cs[0]["word_text"],
                    "candidates": sorted(labels),
                }
            )

    notes: List[str] = list(d.notes)
    if d.scope == "within_image":
        notes.append(
            f"Segmenter mis-split parent image into {len(members)} identical crops; "
            f"keep survivor, drop the rest."
        )
    if skipped:
        notes.append(
            f"{len(skipped)} gap word(s) had VALID disagreement across dropped "
            f"copies and were left unlabeled (optional label-quality pass)."
        )
    action = "consolidate_then_drop" if gap_fills else "drop_redundant"

    return MergeResolution(
        group_id=d.group_id,
        scope=d.scope,
        survivor=survivor,
        survivor_label_count=sm.n_canonical_labels,
        receipts_to_drop=drop,
        gap_fills=gap_fills,
        skipped_gap_disagreements=skipped,
        action=action,
        notes=notes,
    )


def resolve_all(dossiers: List[MergeDossier]) -> List[MergeResolution]:
    return [resolve_dossier(d) for d in dossiers]

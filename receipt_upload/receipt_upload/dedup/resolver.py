"""Stage 2 — deterministic duplicate MERGE plan (survivor + VALID gap-fills).

Merging duplicates is a dedup problem, not a label-adjudication problem. We
pick
the highest-quality copy as the **survivor** and trust its labels. The only
thing the inferior copies can add is a label on a word the survivor *doesn't
label at all* — a **gap-fill**. We only ever copy a ``VALID`` into an empty
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

from receipt_upload.dedup.context import MergeDossier, is_valid_status


@dataclass
class GapFill:
    locus: str  # survivor "line:word" target (concrete for BOTH scopes)
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
    skipped_gaps: List[dict]  # gap words not auto-filled, with a reason
    action: str  # drop_redundant | consolidate_then_drop
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_dossier(d: MergeDossier) -> MergeResolution:
    members = {m.key_str: m for m in d.members}
    survivor = d.survivor_suggested
    sm = members[survivor]
    drop = sorted(k for k in members if k != survivor)

    # A survivor word is OCCUPIED if it already has ANY meaningful label
    # (canonical OR legacy) — we never adjudicate over the survivor's own
    # label.
    occupied = {
        o["pos"] for o in sm.labels if o.get("kind") in ("canonical", "legacy")
    }

    # Byte-identical members (sha256 anchor) are the SAME pixels, so OCR is
    # deterministic and (line:word) positions correspond across copies — even
    # when scope is cross_image (a re-upload under a different image_id). A
    # transaction_identity anchor (near-dup, different pixels) gets no such
    # guarantee and stays text-only.
    byte_identical = d.anchor == "receipt_sha256"

    def survivor_target(o: dict):
        """Survivor (line:word) a dropped observation lands on, or None.

        within_image: pixels identical, so the same pos is the exact target.
        byte-identical cross_image: the dropped copy's OWN (line:word) exists
        in the survivor with the same text — target it directly, which pins
        which occurrence of a repeated token to fill (text-matching alone
        can't). Falls back to unique-text matching if the exact position
        somehow isn't present.
        other cross_image (near-dup): match by full word text, but ONLY when
        it occurs EXACTLY ONCE in the survivor — else refuse to guess.
        """
        if d.scope == "within_image":
            return o["pos"]
        positions = sm.word_index.get(o["word_text"])
        if not positions:
            return None
        if byte_identical and o["pos"] in positions:
            return o["pos"]
        if len(positions) != 1:
            return None
        return positions[0]

    # collect VALID canonical labels from dropped copies, keyed by survivor
    # target
    candidates: Dict[str, List[dict]] = {}
    skipped: List[dict] = []
    for k in drop:
        for o in members[k].labels:
            if o.get("kind") != "canonical" or not is_valid_status(
                o.get("validation_status")
            ):
                continue
            tp = survivor_target(o)
            if tp is None:
                skipped.append(
                    {
                        "word_text": o["word_text"],
                        "label": o["canonical_label"],
                        "from_member": k,
                        "reason": "no_unique_survivor_target",
                    }
                )
                continue
            if tp in occupied:
                continue  # survivor already labels this word -> survivor wins
            candidates.setdefault(tp, []).append(
                {
                    "label": o["canonical_label"],
                    "from": k,
                    "word_text": o["word_text"],
                }
            )

    gap_fills: List[GapFill] = []
    for pos, cs in candidates.items():
        labels = {c["label"] for c in cs}
        if len(labels) == 1:
            c = cs[0]
            gap_fills.append(
                GapFill(
                    locus=pos,
                    word_text=c["word_text"],
                    label=c["label"],
                    from_member=c["from"],
                )
            )
        else:
            # dropped copies VALID-disagree on the same survivor word: don't
            # guess.
            skipped.append(
                {
                    "locus": pos,
                    "word_text": cs[0]["word_text"],
                    "candidates": sorted(labels),
                    "reason": "valid_disagreement",
                }
            )

    notes: List[str] = list(d.notes)
    if d.scope == "within_image":
        notes.append(
            f"Segmenter mis-split parent image into "
            f"{len(members)} identical crops; "
            f"keep survivor, drop the rest."
        )
    if skipped:
        notes.append(
            f"{len(skipped)} gap label(s) left unfilled "
            f"(ambiguous target or VALID "
            f"disagreement) — optional label-quality pass."
        )
    action = "consolidate_then_drop" if gap_fills else "drop_redundant"

    return MergeResolution(
        group_id=d.group_id,
        scope=d.scope,
        survivor=survivor,
        survivor_label_count=sm.n_canonical_labels,
        receipts_to_drop=drop,
        gap_fills=gap_fills,
        skipped_gaps=skipped,
        action=action,
        notes=notes,
    )


def resolve_all(dossiers: List[MergeDossier]) -> List[MergeResolution]:
    return [resolve_dossier(d) for d in dossiers]

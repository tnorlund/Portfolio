#!/usr/bin/env python3
"""Restore the QA'd ReceiptSection corpus after a destructive re-OCR migration.

Background
----------
dev's OCR entities (lines/words) were replaced wholesale by a re-OCR migration.
Re-OCR assigns brand-new ``line_id``s and can RE-SEGMENT text (merge two source
lines into one, split one into two, or just re-read a line with slightly
different characters). The QA'd ``ReceiptSection`` corpus (4,877 rows, ~99%
purity after 3-pass adversarial QA) referenced the OLD ``line_id``s and was
wiped along with the old lines (only 146 rows survived by coincidence).

We still hold the full corpus OFFLINE as "packets": for each receipt, the OLD
lines (``old_line_id -> text``, reading order) and the QA'd sections (type,
OLD ``line_ids``, confidence, model_source, validation_status).

What this tool does
-------------------
1. MATCHER: for one receipt, align its OLD lines (from the packet) to the NEW
   lines (read live from the post-migration table) by normalized edit-distance
   similarity under an order-consistent dynamic-programming alignment. The
   alignment consumes 1..N lines from each side per block, so it natively
   expresses 1:1, 1:N (split), N:1 (merge) and N:M correspondences, plus
   unmatched OLD lines (dropped) and inserted NEW lines. Each OLD line gets its
   matched NEW ``line_id``(s) and a match confidence (the block similarity).

2. REMAPPER: translate each QA'd section's OLD ``line_ids`` -> NEW ``line_ids``
   via the alignment. Sections keep their type/status/model_source (with
   ``+remap-v1`` appended to model_source); confidence is multiplied by the mean
   match confidence of the section's surviving lines. OLD lines whose best match
   is below ``--match-threshold`` are DROPPED and logged. A section that loses
   more than ``--pending-loss-frac`` of its lines is downgraded to PENDING
   (needs re-QA) instead of keeping VALID; an emptied section is skipped.

3. SAFETY: endpoint comes from ``--endpoint-url`` or ``DYNAMODB_ENDPOINT_URL``.
   Writes to a NON-local endpoint require ``--allow-remote`` (the session's
   established guard). ``--dry-run`` is the DEFAULT: nothing is written unless
   ``--apply`` is passed. This is offline-prep tooling; do not ``--apply`` to
   dev until the re-OCR migration has finished.

Commands
--------
  report    Read live NEW lines for every packet receipt, run the full
            match+remap, and print/emit the restoration report. Read-only
            (never writes) regardless of ``--apply``.
  apply     Same match+remap, then WRITE the restored sections (dry-run by
            default; ``--apply`` + ``--allow-remote`` for a real remote write).
  simulate  Self-contained accuracy harness: perturb N packet receipts the way
            a re-OCR would (renumber ids, merge ~10%, split ~5%, OCR-noise ~10%
            of texts), remap against the perturbed state, and measure how many
            sections are restored with correct membership vs ground truth. Needs
            no live table.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

LOG = logging.getLogger("remap_sections")

# Endpoints that are safe to write to without --allow-remote.
LOCAL_ENDPOINT_HINTS = ("127.0.0.1", "localhost", "0.0.0.0", "192.168.")

# model_source suffix stamped on every remapped row.
REMAP_TAG = "+remap-v1"


# --------------------------------------------------------------------------- #
# Text normalization + similarity                                             #
# --------------------------------------------------------------------------- #


_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip, and collapse internal whitespace for matching.

    Case and whitespace are noise for line-identity purposes; a re-OCR that
    re-flows spacing must not read as a different line.
    """
    return _WS_RE.sub(" ", (text or "").strip().lower())


def levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein edit distance (insert/delete/substitute = 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Two-row DP; keep the shorter string as the inner (column) axis.
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


# Similarity below which a pair is a non-match: we don't need its exact value,
# only that it loses to a real match, so pairs whose length ratio already caps
# similarity here skip the (expensive) edit-distance entirely.
_LOW_SIM = 0.35


def similarity_norm(na: str, nb: str) -> float:
    """Similarity of two ALREADY-normalized strings; see :func:`similarity`.

    Fast paths: identical strings short-circuit to 1.0, and a length-ratio
    UPPER bound (edit distance >= length difference) lets clearly-dissimilar
    pairs return that bound without running Levenshtein -- the bound is <=
    ``_LOW_SIM`` there, so such a non-match can never outrank a real match.
    """
    if na == nb:
        return 1.0
    la, lb = len(na), len(nb)
    denom = la if la >= lb else lb
    if denom == 0:
        return 1.0
    upper = 1.0 - abs(la - lb) / denom  # sim <= this (dist >= |la-lb|)
    if upper <= _LOW_SIM:
        return upper
    return 1.0 - levenshtein(na, nb) / denom


def similarity(a: str, b: str) -> float:
    """Normalized-edit-distance similarity in [0, 1] over normalized text.

    1.0 == identical after normalization; 0.0 == fully dissimilar. Two empty
    strings are treated as identical (1.0).
    """
    return similarity_norm(normalize_text(a), normalize_text(b))


# --------------------------------------------------------------------------- #
# Matcher: order-consistent DP alignment                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AlignBlock:
    """One aligned block between OLD and NEW line sequences.

    ``old_ids``/``new_ids`` are the OLD/NEW ``line_id``s consumed by the block.
    Either side may be empty: an empty ``new_ids`` is an unmatched OLD line
    (dropped); an empty ``old_ids`` is an inserted NEW line (no OLD source).
    ``score`` is the block similarity in [0, 1].
    """

    old_ids: tuple[int, ...]
    new_ids: tuple[int, ...]
    score: float

    @property
    def kind(self) -> str:
        a, b = len(self.old_ids), len(self.new_ids)
        if a == 0:
            return "inserted"  # NEW line with no OLD source
        if b == 0:
            return "unmatched"  # OLD line dropped
        if a == 1 and b == 1:
            return "1:1"
        if a == 1 and b >= 2:
            return "1:N"  # OLD line split into several NEW lines
        if a >= 2 and b == 1:
            return "N:1"  # several OLD lines merged into one NEW line
        return "N:M"


# A block that maps some OLD lines to no NEW line still "costs" something so the
# DP does not drop lines for free; an unmatched OLD line scores this.
_UNMATCHED_SCORE = 0.0
_INSERTED_SCORE = 0.0


def _band_bounds(m: int, n: int, band: int | None) -> tuple[int, int]:
    """(lo, hi) allowed range for the offset ``i - j`` in the banded DP.

    Re-OCR preserves reading order and drifts the line count only by the net of
    merges/splits, so the optimal alignment hugs the diagonal from ``(0, 0)`` to
    ``(m, n)``. Restricting ``i - j`` to a band around that diagonal turns the
    DP from O(m*n) to O(m*band) and bounds the edit-distance calls, which is
    what makes the live 799-receipt report tractable. The band always includes
    both endpoints' offsets (0 and ``m - n``), so a valid full path exists.
    """
    if band is None:
        # The offset i-j drifts monotonically by (#merges - #splits) seen so
        # far, i.e. ~O(merge_rate * n); ~15% of the line count plus a fixed
        # slack comfortably covers realistic re-OCR churn while keeping the DP
        # near-linear. (Widen via the ``band`` arg if a receipt is pathological.)
        band = max(12, (3 * max(m, n)) // 20)  # 15% drift tolerance, min 12
    lo = min(0, m - n) - band
    hi = max(0, m - n) + band
    return lo, hi


def align_lines(
    old_lines: Sequence[tuple[int, str]],
    new_lines: Sequence[tuple[int, str]],
    max_block: int = 3,
    merge_join: str = " ",
    band: int | None = None,
) -> list[AlignBlock]:
    """Monotonic block alignment of OLD lines to NEW lines.

    Both inputs are ``(line_id, text)`` in reading order. The DP over
    ``(i, j)`` (OLD/NEW lines consumed) may, at each step, emit a block that
    consumes ``a in 0..max_block`` OLD and ``b in 0..max_block`` NEW lines
    (not both zero). A block's score is the similarity between the joined OLD
    text and the joined NEW text, so a 2:1 block is scored as "do the two OLD
    lines concatenated look like this one NEW line" -- exactly the merge case.
    Pure-OLD (``b==0``) and pure-NEW (``a==0``) blocks model a dropped OLD line
    and an inserted NEW line. Total score is maximized; ties break toward
    smaller blocks (prefer 1:1 over a stretched merge/split).

    The DP is banded around the diagonal (see ``_band_bounds``) so cost is
    O(m * band) rather than O(m * n); the band always contains a full path.

    Returns the list of blocks in reading order.
    """
    m, n = len(old_lines), len(new_lines)
    # Normalize each line ONCE (the per-call regex dominated the DP otherwise).
    old_norm = [normalize_text(t) for _, t in old_lines]
    new_norm = [normalize_text(t) for _, t in new_lines]
    lo, hi = _band_bounds(m, n, band)

    def in_band(i: int, j: int) -> bool:
        return lo <= (i - j) <= hi

    NEG = float("-inf")
    # best[i][j] = best total score aligning old_lines[i:] with new_lines[j:].
    best = [[NEG] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[int, int] | None]] = [
        [None] * (n + 1) for _ in range(m + 1)
    ]
    best[m][n] = 0.0

    def block_score(i: int, a: int, j: int, b: int) -> float:
        if a == 0:
            return _INSERTED_SCORE
        if b == 0:
            return _UNMATCHED_SCORE
        left = merge_join.join(old_norm[i : i + a])
        right = merge_join.join(new_norm[j : j + b])
        s = similarity_norm(left, right)
        # Mild size penalty so identical-scoring bigger blocks lose to 1:1.
        return s - 0.001 * (a + b - 2)

    # Fill from the end backwards, only within the band.
    for i in range(m, -1, -1):
        for j in range(n, -1, -1):
            if i == m and j == n:
                continue
            if not in_band(i, j):
                continue
            best_val = NEG
            best_step: tuple[int, int] | None = None
            for a in range(0, max_block + 1):
                if i + a > m:
                    break
                for b in range(0, max_block + 1):
                    if j + b > n:
                        break
                    if a == 0 and b == 0:
                        continue
                    if not in_band(i + a, j + b):
                        continue
                    nxt = best[i + a][j + b]
                    if nxt == NEG:
                        continue
                    val = block_score(i, a, j, b) + nxt
                    if val > best_val:
                        best_val = val
                        best_step = (a, b)
            best[i][j] = best_val
            back[i][j] = best_step

    # Backtrack.
    blocks: list[AlignBlock] = []
    i, j = 0, 0
    while i < m or j < n:
        step = back[i][j]
        if step is None:
            # Should not happen (a full path always exists), but stay safe:
            # consume whatever remains as a final unmatched/inserted block.
            step = (min(1, m - i), min(1, n - j)) if (m - i or n - j) else (0, 0)
            if step == (0, 0):
                break
        a, b = step
        old_ids = tuple(old_lines[i + k][0] for k in range(a))
        new_ids = tuple(new_lines[j + k][0] for k in range(b))
        score = (
            similarity(
                merge_join.join(old_lines[i + k][1] for k in range(a)),
                merge_join.join(new_lines[j + k][1] for k in range(b)),
            )
            if a and b
            else (_UNMATCHED_SCORE if b == 0 else _INSERTED_SCORE)
        )
        blocks.append(AlignBlock(old_ids=old_ids, new_ids=new_ids, score=score))
        i += a
        j += b
    return blocks


@dataclass
class LineMatch:
    """Per-OLD-line result: matched NEW ids + confidence + block kind."""

    new_ids: list[int]
    confidence: float
    kind: str


def old_line_matches(blocks: Iterable[AlignBlock]) -> dict[int, LineMatch]:
    """Map every OLD ``line_id`` to its matched NEW ids + confidence.

    An unmatched OLD line (block with empty ``new_ids``) maps to an empty list
    at confidence 0.0. Inserted NEW lines (empty ``old_ids``) are ignored here.
    """
    out: dict[int, LineMatch] = {}
    for blk in blocks:
        for old_id in blk.old_ids:
            out[old_id] = LineMatch(
                new_ids=list(blk.new_ids),
                confidence=blk.score,
                kind=blk.kind,
            )
    return out


# --------------------------------------------------------------------------- #
# Remapper                                                                     #
# --------------------------------------------------------------------------- #

VALID = "VALID"
PENDING = "PENDING"


@dataclass
class SectionInput:
    """A QA'd section as stored in the offline packet (OLD line_ids)."""

    section_type: str
    line_ids: list[int]
    confidence: float | None
    model_source: str | None
    validation_status: str | None


@dataclass
class RemappedSection:
    """The result of remapping one section onto NEW line_ids."""

    section_type: str
    new_line_ids: list[int]
    confidence: float | None
    model_source: str | None
    validation_status: str | None
    dropped_old_ids: list[int]
    kept_old_ids: list[int]
    lost_frac: float
    downgraded: bool  # VALID -> PENDING because too many lines were lost


def remap_section(
    section: SectionInput,
    matches: dict[int, LineMatch],
    match_threshold: float,
    pending_loss_frac: float,
) -> RemappedSection | None:
    """Remap one section's OLD line_ids to NEW line_ids via the alignment.

    Returns ``None`` (and logs) if the section is emptied by the remap. OLD
    lines with confidence < ``match_threshold`` (or no match at all) are
    dropped. If the fraction of dropped lines exceeds ``pending_loss_frac`` a
    VALID section is downgraded to PENDING; other statuses are preserved.
    Confidence is the original confidence times the mean match confidence of
    the surviving lines.
    """
    kept_old: list[int] = []
    dropped_old: list[int] = []
    new_ids: list[int] = []
    kept_conf: list[float] = []
    seen: set[int] = set()

    for old_id in section.line_ids:
        match = matches.get(old_id)
        if match is None or not match.new_ids or match.confidence < match_threshold:
            dropped_old.append(old_id)
            continue
        kept_old.append(old_id)
        kept_conf.append(match.confidence)
        for nid in match.new_ids:
            if nid not in seen:
                seen.add(nid)
                new_ids.append(nid)

    total = len(section.line_ids)
    lost_frac = (len(dropped_old) / total) if total else 1.0

    if not new_ids:
        LOG.info(
            "section %s emptied by remap (all %d line(s) dropped) -> skipped",
            section.section_type,
            total,
        )
        return None

    mean_match = sum(kept_conf) / len(kept_conf) if kept_conf else 0.0
    new_conf = (
        None
        if section.confidence is None
        else max(0.0, min(1.0, section.confidence * mean_match))
    )

    status = section.validation_status
    downgraded = False
    if lost_frac > pending_loss_frac:
        if (status or "").upper() == VALID:
            status = PENDING
            downgraded = True

    model_source = (section.model_source or "") + REMAP_TAG

    if dropped_old:
        LOG.info(
            "section %s dropped %d/%d line(s) %s (lost_frac=%.2f%s)",
            section.section_type,
            len(dropped_old),
            total,
            dropped_old,
            lost_frac,
            ", DOWNGRADED->PENDING" if downgraded else "",
        )

    return RemappedSection(
        section_type=section.section_type,
        new_line_ids=sorted(new_ids),
        confidence=new_conf,
        model_source=model_source,
        validation_status=status,
        dropped_old_ids=dropped_old,
        kept_old_ids=kept_old,
        lost_frac=lost_frac,
        downgraded=downgraded,
    )


# --------------------------------------------------------------------------- #
# Per-receipt driver + stats                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class ReceiptResult:
    image_id: str
    receipt_id: int
    blocks: list[AlignBlock]
    remapped: list[RemappedSection]
    skipped_sections: int
    matched: bool  # did we find NEW lines to align against?


@dataclass
class Stats:
    receipts_total: int = 0
    receipts_matched: int = 0
    receipts_no_new_lines: int = 0
    # line alignment counts
    align_1_1: int = 0
    align_1_n: int = 0
    align_n_1: int = 0
    align_n_m: int = 0
    align_unmatched: int = 0
    align_inserted: int = 0
    # section outcomes
    sections_valid: int = 0
    sections_pending: int = 0
    sections_other: int = 0
    sections_lost: int = 0  # emptied / skipped

    def tally_blocks(self, blocks: Iterable[AlignBlock]) -> None:
        for blk in blocks:
            k = blk.kind
            if k == "1:1":
                self.align_1_1 += 1
            elif k == "1:N":
                self.align_1_n += 1
            elif k == "N:1":
                self.align_n_1 += 1
            elif k == "N:M":
                self.align_n_m += 1
            elif k == "unmatched":
                self.align_unmatched += 1
            elif k == "inserted":
                self.align_inserted += 1

    def tally_receipt(self, res: ReceiptResult) -> None:
        self.receipts_total += 1
        if not res.matched:
            self.receipts_no_new_lines += 1
            self.sections_lost += res.skipped_sections
            return
        self.receipts_matched += 1
        self.tally_blocks(res.blocks)
        for rm in res.remapped:
            status = (rm.validation_status or "").upper()
            if status == PENDING:
                self.sections_pending += 1
            elif status == VALID:
                self.sections_valid += 1
            else:
                self.sections_other += 1
        self.sections_lost += res.skipped_sections

    def as_dict(self) -> dict[str, Any]:
        total_remappable = (
            self.sections_valid
            + self.sections_pending
            + self.sections_other
            + self.sections_lost
        )
        return {
            "receipts_total": self.receipts_total,
            "receipts_matched": self.receipts_matched,
            "receipts_no_new_lines": self.receipts_no_new_lines,
            "lines_aligned": {
                "1:1": self.align_1_1,
                "1:N_split": self.align_1_n,
                "N:1_merge": self.align_n_1,
                "N:M": self.align_n_m,
                "unmatched_old": self.align_unmatched,
                "inserted_new": self.align_inserted,
            },
            "sections": {
                "total_remappable": total_remappable,
                "restored_valid": self.sections_valid,
                "restored_pending": self.sections_pending,
                "restored_other_status": self.sections_other,
                "lost_emptied": self.sections_lost,
            },
        }


def _parse_packet_sections(packet: dict[str, Any]) -> list[SectionInput]:
    out: list[SectionInput] = []
    for s in packet.get("sections", []):
        out.append(
            SectionInput(
                section_type=s["section_type"],
                line_ids=[int(x) for x in s["line_ids"]],
                confidence=s.get("confidence"),
                model_source=s.get("model_source"),
                validation_status=s.get("validation_status"),
            )
        )
    return out


def _packet_old_lines(packet: dict[str, Any]) -> list[tuple[int, str]]:
    """OLD lines from a packet as ``(line_id, text)`` in reading order."""
    lines = packet.get("lines", {})
    pairs = [(int(k), v) for k, v in lines.items()]
    pairs.sort(key=lambda p: p[0])
    return pairs


def remap_receipt(
    packet: dict[str, Any],
    new_lines: Sequence[tuple[int, str]],
    match_threshold: float,
    pending_loss_frac: float,
    max_block: int = 3,
) -> ReceiptResult:
    """Align + remap a single receipt's packet against its NEW lines."""
    old_lines = _packet_old_lines(packet)
    sections = _parse_packet_sections(packet)

    if not new_lines:
        return ReceiptResult(
            image_id=packet["image_id"],
            receipt_id=int(packet["receipt_id"]),
            blocks=[],
            remapped=[],
            skipped_sections=len(sections),
            matched=False,
        )

    blocks = align_lines(old_lines, new_lines, max_block=max_block)
    matches = old_line_matches(blocks)

    remapped: list[RemappedSection] = []
    skipped = 0
    for section in sections:
        rm = remap_section(section, matches, match_threshold, pending_loss_frac)
        if rm is None:
            skipped += 1
        else:
            remapped.append(rm)

    return ReceiptResult(
        image_id=packet["image_id"],
        receipt_id=int(packet["receipt_id"]),
        blocks=blocks,
        remapped=remapped,
        skipped_sections=skipped,
        matched=True,
    )


# --------------------------------------------------------------------------- #
# Live table access (report / apply)                                           #
# --------------------------------------------------------------------------- #


def is_local_endpoint(endpoint_url: str | None) -> bool:
    return bool(endpoint_url) and any(h in endpoint_url for h in LOCAL_ENDPOINT_HINTS)


def _resolve_endpoint(cli_endpoint: str | None) -> str | None:
    return cli_endpoint or os.environ.get("DYNAMODB_ENDPOINT_URL")


def _make_dynamo_client(table_name: str, endpoint_url: str | None, region: str):
    """Build a DynamoClient (import kept local so the pure paths need no boto)."""
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    dynamo_pkg = repo_root / "receipt_dynamo"
    if str(dynamo_pkg) not in sys.path:
        sys.path.insert(0, str(dynamo_pkg))
    from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402

    return DynamoClient(table_name, region=region, endpoint_url=endpoint_url)


def read_new_lines(
    client: Any, image_id: str, receipt_id: int
) -> list[tuple[int, str]]:
    """Read the live (post-migration) NEW lines for a receipt, reading order."""
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    pairs = [(int(l.line_id), l.text) for l in lines]
    pairs.sort(key=lambda p: p[0])
    return pairs


def _build_receipt_sections(result: ReceiptResult, created_at: datetime) -> list[Any]:
    """Turn RemappedSection rows into ReceiptSection entities for a write."""
    from receipt_dynamo.entities.receipt_section import ReceiptSection  # noqa: E402

    rows: list[Any] = []
    for rm in result.remapped:
        rows.append(
            ReceiptSection(
                receipt_id=result.receipt_id,
                image_id=result.image_id,
                section_type=rm.section_type,
                line_ids=rm.new_line_ids,
                created_at=created_at,
                confidence=rm.confidence,
                model_source=rm.model_source,
                validation_status=rm.validation_status,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #


def run_report(
    packets: list[dict[str, Any]],
    client: Any,
    match_threshold: float,
    pending_loss_frac: float,
    max_block: int = 3,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Read live NEW lines for every packet, remap, and summarize. Read-only.

    Produces the "what would be restored on real dev" report without writing
    anything. ``client`` must expose ``list_receipt_lines_from_receipt``.
    """
    stats = Stats()
    per_receipt: list[dict[str, Any]] = []
    for idx, packet in enumerate(packets, start=1):
        image_id = packet["image_id"]
        receipt_id = int(packet["receipt_id"])
        try:
            new_lines = read_new_lines(client, image_id, receipt_id)
        except Exception as exc:  # pragma: no cover - live path
            LOG.warning(
                "failed to read NEW lines for %s#%s: %s", image_id, receipt_id, exc
            )
            new_lines = []
        result = remap_receipt(
            packet, new_lines, match_threshold, pending_loss_frac, max_block
        )
        stats.tally_receipt(result)
        per_receipt.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "matched": result.matched,
                "sections_restored": len(result.remapped),
                "sections_skipped": result.skipped_sections,
                "sections_downgraded": sum(
                    1 for r in result.remapped if r.downgraded
                ),
            }
        )
        if progress_every and idx % progress_every == 0:
            LOG.info(
                "report progress: %d/%d receipts (matched=%d)",
                idx,
                len(packets),
                stats.receipts_matched,
            )
    return {"stats": stats.as_dict(), "per_receipt": per_receipt}


def print_report(report: dict[str, Any]) -> None:
    s = report["stats"]
    la = s["lines_aligned"]
    sc = s["sections"]
    print("=== Receipts ===")
    print(
        f"  total={s['receipts_total']} matched={s['receipts_matched']} "
        f"no_new_lines={s['receipts_no_new_lines']}"
    )
    print("=== Lines aligned ===")
    print(
        f"  1:1={la['1:1']} 1:N_split={la['1:N_split']} N:1_merge={la['N:1_merge']} "
        f"N:M={la['N:M']} unmatched_old={la['unmatched_old']} "
        f"inserted_new={la['inserted_new']}"
    )
    print("=== Sections ===")
    print(
        f"  total_remappable={sc['total_remappable']} "
        f"restored_valid={sc['restored_valid']} "
        f"restored_pending={sc['restored_pending']} "
        f"restored_other_status={sc['restored_other_status']} "
        f"lost_emptied={sc['lost_emptied']}"
    )


# --------------------------------------------------------------------------- #
# Apply (guarded; dry-run default)                                             #
# --------------------------------------------------------------------------- #


def run_apply(
    packets: list[dict[str, Any]],
    table_name: str,
    endpoint_url: str | None,
    region: str,
    match_threshold: float,
    pending_loss_frac: float,
    apply: bool,
    allow_remote: bool,
    max_block: int = 3,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Match+remap every packet and (optionally) WRITE restored sections.

    Safety: writing to a non-local endpoint requires ``allow_remote``.
    ``apply`` is required to write at all; otherwise this is a dry run that
    reports exactly what WOULD be written.
    """
    if apply and not is_local_endpoint(endpoint_url) and not allow_remote:
        raise SystemExit(
            "REFUSING to write to a non-local endpoint without --allow-remote. "
            f"(endpoint_url={endpoint_url!r}) Re-run against a local table, or "
            "pass --allow-remote to deliberately target remote dev/prod."
        )

    client = _make_dynamo_client(table_name, endpoint_url, region)
    created_at = datetime.now(timezone.utc)
    stats = Stats()
    written = 0
    would_write = 0
    for idx, packet in enumerate(packets, start=1):
        image_id = packet["image_id"]
        receipt_id = int(packet["receipt_id"])
        try:
            new_lines = read_new_lines(client, image_id, receipt_id)
        except Exception as exc:  # pragma: no cover - live path
            LOG.warning("read NEW lines failed %s#%s: %s", image_id, receipt_id, exc)
            new_lines = []
        result = remap_receipt(
            packet, new_lines, match_threshold, pending_loss_frac, max_block
        )
        stats.tally_receipt(result)
        rows = _build_receipt_sections(result, created_at)
        would_write += len(rows)
        if apply and rows:
            client.add_receipt_sections(rows)
            written += len(rows)
        if progress_every and idx % progress_every == 0:
            LOG.info(
                "apply progress: %d/%d receipts (%s=%d)",
                idx,
                len(packets),
                "written" if apply else "would_write",
                written if apply else would_write,
            )
    return {
        "stats": stats.as_dict(),
        "apply": apply,
        "endpoint_url": endpoint_url,
        "sections_written": written,
        "sections_would_write": would_write,
    }


# --------------------------------------------------------------------------- #
# Simulation harness (accuracy test, no live table)                            #
# --------------------------------------------------------------------------- #


OCR_SUBST = {
    "o": "0",
    "O": "0",
    "l": "1",
    "I": "1",
    "i": "1",
    "s": "5",
    "S": "5",
    "b": "6",
    "g": "9",
    "B": "8",
    "z": "2",
    "e": "c",
    "t": "f",
    "n": "m",
    "u": "v",
    "1": "l",
    "0": "o",
    "5": "s",
}


def ocr_noise(text: str, rng: random.Random, rate: float = 0.15) -> str:
    """Apply per-character OCR-style substitutions at ``rate`` probability."""
    out = []
    for ch in text:
        if ch in OCR_SUBST and rng.random() < rate:
            out.append(OCR_SUBST[ch])
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class SimResult:
    new_lines: list[tuple[int, str]]
    # ground-truth OLD line_id -> the NEW line_id(s) it truly became
    truth: dict[int, list[int]]


def simulate_reocr(
    old_lines: Sequence[tuple[int, str]],
    rng: random.Random,
    merge_frac: float = 0.10,
    split_frac: float = 0.05,
    noise_frac: float = 0.10,
    char_noise_rate: float = 0.25,
    id_base: int = 1000,
) -> SimResult:
    """Perturb OLD lines the way a re-OCR would; return NEW lines + truth map.

    - renumber: every NEW line gets a fresh id (``id_base`` + running counter),
      unrelated to the OLD id.
    - merge (~merge_frac of lines): fuse a line with the next into one NEW line.
    - split (~split_frac): break a line into two NEW lines at a space.
    - noise: ~``noise_frac`` of the resulting NEW lines are OCR-corrupted, each
      chosen line getting per-character substitutions at ``char_noise_rate``
      (so a corrupted line is visibly misread, mirroring real OCR errors on a
      subset of lines rather than a uniform smear over all text).

    ``truth[old_id]`` is the exact set of NEW ids that OLD line became -- the
    oracle the accuracy measurement compares the matcher against.
    """
    n = len(old_lines)
    # Decide, per index, whether it starts a merge with the next line.
    merge_flags = [False] * n
    i = 0
    while i < n - 1:
        if rng.random() < merge_frac:
            merge_flags[i] = True
            i += 2  # consume this line and the next; don't chain
        else:
            i += 1
    split_flags = [
        (not merge_flags[k])
        and (k == 0 or not merge_flags[k - 1])
        and rng.random() < split_frac
        for k in range(n)
    ]

    # Build NEW lines (renumbered, merged, split) WITHOUT noise first.
    new_lines: list[tuple[int, str]] = []
    truth: dict[int, list[int]] = {}
    next_id = id_base
    k = 0
    while k < n:
        old_id, text = old_lines[k]
        if merge_flags[k] and k + 1 < n:
            nid = next_id
            next_id += 1
            nxt_id, nxt_text = old_lines[k + 1]
            new_lines.append((nid, f"{text} {nxt_text}"))
            truth[old_id] = [nid]
            truth[nxt_id] = [nid]
            k += 2
            continue
        if split_flags[k] and " " in text.strip():
            words = text.split(" ")
            mid = max(1, len(words) // 2)
            nid1, nid2 = next_id, next_id + 1
            next_id += 2
            new_lines.append((nid1, " ".join(words[:mid])))
            new_lines.append((nid2, " ".join(words[mid:])))
            truth[old_id] = [nid1, nid2]
            k += 1
            continue
        nid = next_id
        next_id += 1
        new_lines.append((nid, text))
        truth[old_id] = [nid]
        k += 1

    # OCR-corrupt ~noise_frac of the resulting NEW lines.
    idx_pool = list(range(len(new_lines)))
    rng.shuffle(idx_pool)
    n_noisy = int(round(noise_frac * len(new_lines)))
    for idx in idx_pool[:n_noisy]:
        nid, txt = new_lines[idx]
        new_lines[idx] = (nid, ocr_noise(txt, rng, char_noise_rate))

    return SimResult(new_lines=new_lines, truth=truth)


def run_simulation(
    packets: list[dict[str, Any]],
    n_receipts: int,
    seed: int,
    match_threshold: float,
    pending_loss_frac: float,
    merge_frac: float = 0.10,
    split_frac: float = 0.05,
    noise_frac: float = 0.10,
    max_block: int = 3,
) -> dict[str, Any]:
    """End-to-end accuracy test against a SIMULATED post-migration state.

    For ``n_receipts`` packets, perturb the lines, remap, and compare the
    restored section membership to ground truth (the union of each OLD line's
    true NEW ids). Reports section-level restoration accuracy and line-alignment
    accuracy.
    """
    rng = random.Random(seed)
    # Prefer receipts that actually carry sections and enough lines to perturb.
    candidates = [
        p for p in packets if p.get("sections") and len(p.get("lines", {})) >= 4
    ]
    rng.shuffle(candidates)
    chosen = candidates[:n_receipts]

    sections_total = 0
    membership_correct = 0
    membership_correct_valid = 0
    restored_valid = 0
    restored_pending = 0
    lost = 0
    line_total = 0
    line_correct = 0
    align: Counter = Counter()
    per_receipt: list[dict[str, Any]] = []

    for packet in chosen:
        old_lines = _packet_old_lines(packet)
        sim = simulate_reocr(
            old_lines,
            rng,
            merge_frac=merge_frac,
            split_frac=split_frac,
            noise_frac=noise_frac,
        )
        result = remap_receipt(
            packet, sim.new_lines, match_threshold, pending_loss_frac, max_block
        )
        for blk in result.blocks:
            align[blk.kind] += 1

        # line-level alignment accuracy: predicted new-id set vs truth
        matches = old_line_matches(result.blocks)
        for old_id, truth_ids in sim.truth.items():
            line_total += 1
            pred = set(matches.get(old_id, LineMatch([], 0.0, "unmatched")).new_ids)
            if pred == set(truth_ids):
                line_correct += 1

        # section-level: compare remapped membership to ground-truth membership.
        # Ground truth resolves each OLD line to its true NEW ids; a section can
        # legitimately have >1 OLD line hit the same merged NEW id, so use sets.
        remapped_list = list(result.remapped)
        remapped_by_type: dict[str, list[RemappedSection]] = {}
        for r in remapped_list:
            remapped_by_type.setdefault(r.section_type, []).append(r)
        for sec in _parse_packet_sections(packet):
            sections_total += 1
            gt_ids: set[int] = set()
            for oid in sec.line_ids:
                gt_ids.update(sim.truth.get(oid, []))
            bucket = remapped_by_type.get(sec.section_type)
            rm = bucket.pop(0) if bucket else None
            if rm is None:
                lost += 1
                continue
            status = (rm.validation_status or "").upper()
            if status == VALID:
                restored_valid += 1
            elif status == PENDING:
                restored_pending += 1
            correct = set(rm.new_line_ids) == gt_ids
            if correct:
                membership_correct += 1
                if status == VALID:
                    membership_correct_valid += 1
        per_receipt.append(
            {
                "image_id": packet["image_id"],
                "receipt_id": int(packet["receipt_id"]),
                "old_lines": len(old_lines),
                "new_lines": len(sim.new_lines),
                "sections": len(packet.get("sections", [])),
                "restored": len(remapped_list),
            }
        )

    def pct(a: int, b: int) -> float:
        return round(100.0 * a / b, 2) if b else 0.0

    return {
        "receipts_tested": len(chosen),
        "seed": seed,
        "perturbation": {
            "merge_frac": merge_frac,
            "split_frac": split_frac,
            "noise_frac": noise_frac,
        },
        "line_alignment": {
            "total": line_total,
            "correct": line_correct,
            "accuracy_pct": pct(line_correct, line_total),
            "by_kind": dict(align),
        },
        "sections": {
            "total": sections_total,
            "restored_valid": restored_valid,
            "restored_pending": restored_pending,
            "lost": lost,
            "membership_correct": membership_correct,
            "membership_correct_valid": membership_correct_valid,
            "membership_accuracy_pct": pct(membership_correct, sections_total),
            "valid_membership_accuracy_pct": pct(
                membership_correct_valid, sections_total
            ),
        },
        "per_receipt": per_receipt,
    }


def print_simulation(report: dict[str, Any]) -> None:
    la = report["line_alignment"]
    sc = report["sections"]
    print(f"=== Simulated re-OCR restoration (seed={report['seed']}) ===")
    print(
        f"  receipts_tested={report['receipts_tested']} "
        f"perturbation={report['perturbation']}"
    )
    print("=== Line alignment ===")
    print(
        f"  accuracy={la['accuracy_pct']}%  ({la['correct']}/{la['total']})  "
        f"by_kind={la['by_kind']}"
    )
    print("=== Section restoration ===")
    print(
        f"  total={sc['total']} restored_valid={sc['restored_valid']} "
        f"restored_pending={sc['restored_pending']} lost={sc['lost']}"
    )
    print(
        f"  membership_accuracy={sc['membership_accuracy_pct']}% "
        f"(correct={sc['membership_correct']})"
    )
    print(
        f"  valid+correct membership={sc['valid_membership_accuracy_pct']}% "
        f"(={sc['membership_correct_valid']})"
    )


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


DEFAULT_PACKETS = Path(
    "/private/tmp/claude-501/-Users-tnorlund-vegas-26/"
    "8e37906b-7da3-4cc8-b685-505d5d59c685/scratchpad/section_qa/post_packets.json"
)


def load_packets(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise SystemExit(f"packets file must be a JSON list, got {type(data)}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--packets",
        type=Path,
        default=DEFAULT_PACKETS,
        help="offline QA'd section packets JSON (list of receipts)",
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        default=0.5,
        help="min per-line match similarity to keep a line in a section",
    )
    parser.add_argument(
        "--pending-loss-frac",
        type=float,
        default=0.30,
        help="a VALID section losing more than this fraction of its lines is "
        "downgraded to PENDING (needs re-QA)",
    )
    parser.add_argument("--max-block", type=int, default=3)
    parser.add_argument("--json", type=Path, default=None, help="write report JSON")
    parser.add_argument("--limit", type=int, default=None, help="cap #packets")

    sub = parser.add_subparsers(dest="command", required=True)

    rep = sub.add_parser(
        "report", help="read live NEW lines + summarize what would be restored"
    )
    rep.add_argument("--table-name", required=True)
    rep.add_argument("--endpoint-url", default=None)
    rep.add_argument("--region", default="us-east-1")

    ap = sub.add_parser(
        "apply", help="remap and (optionally) write restored sections (guarded)"
    )
    ap.add_argument("--table-name", required=True)
    ap.add_argument("--endpoint-url", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually WRITE (default is a dry run that only reports)",
    )
    ap.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit writing to a NON-local endpoint (dev/prod)",
    )

    sim = sub.add_parser(
        "simulate", help="accuracy test against a simulated post-migration state"
    )
    sim.add_argument("--n-receipts", type=int, default=20)
    sim.add_argument("--seed", type=int, default=1234)
    sim.add_argument("--merge-frac", type=float, default=0.10)
    sim.add_argument("--split-frac", type=float, default=0.05)
    sim.add_argument("--noise-frac", type=float, default=0.10)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(levelname)s %(message)s"
    )

    packets = load_packets(args.packets)
    if args.limit:
        packets = packets[: args.limit]

    if args.command == "report":
        endpoint = _resolve_endpoint(args.endpoint_url)
        client = _make_dynamo_client(args.table_name, endpoint, args.region)
        report = run_report(
            packets,
            client,
            args.match_threshold,
            args.pending_loss_frac,
            args.max_block,
        )
        report["endpoint_url"] = endpoint
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        print_report(report)
        return 0

    if args.command == "apply":
        endpoint = _resolve_endpoint(args.endpoint_url)
        report = run_apply(
            packets,
            args.table_name,
            endpoint,
            args.region,
            args.match_threshold,
            args.pending_loss_frac,
            apply=args.apply,
            allow_remote=args.allow_remote,
            max_block=args.max_block,
        )
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        print_report(report)
        mode = "APPLIED" if args.apply else "DRY-RUN"
        print(
            f"=== {mode} === written={report['sections_written']} "
            f"would_write={report['sections_would_write']} "
            f"endpoint={report['endpoint_url']}"
        )
        return 0

    if args.command == "simulate":
        report = run_simulation(
            packets,
            n_receipts=args.n_receipts,
            seed=args.seed,
            match_threshold=args.match_threshold,
            pending_loss_frac=args.pending_loss_frac,
            merge_frac=args.merge_frac,
            split_frac=args.split_frac,
            noise_frac=args.noise_frac,
            max_block=args.max_block,
        )
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        print_simulation(report)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

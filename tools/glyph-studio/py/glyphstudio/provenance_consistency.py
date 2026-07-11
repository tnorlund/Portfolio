"""Compose provenance-consistency pass for the M6 compose pipeline.

The M6 cold-start composer stitches a novel receipt by splicing spliced item
rows from several real "scaffold" receipts onto a base scaffold.  That stitching
leaves three classes of provenance defect that no existing gate catches (see the
adversarial review, findings D1/D5/D6/D12):

  (a) role-cardinality / zone violations -- a singleton header role (cashier,
      phone, date, storefront) gets duplicated because a spliced item block drags
      the *source* receipt's own cashier line into the item zone.  The composer
      never dedupes singleton roles nor checks that a cashier line sits in the
      header zone (before the first item).

  (b) scaffold artifacts copied verbatim -- masked card PANs, REF#, EMV AID, and
      the transaction cryptogram (TC) are memorized byte-for-byte from a real
      transaction.  These must be regenerated synthetically (data hygiene: a
      training example must never carry a real card's auth values).

  (c) compose->render divergence -- the intended line set (the composer's
      ``synthetic_receipt_preview``) and the emitted token stream disagree:
      whole rows (the card-auth block) are dropped, and an inherited TAX /
      EXEMPTION block is stale-copied onto a non-taxable basket whose gross was
      never rescaled.

This module is deliberately *data-driven*: the role cardinality caps and zones
are LEARNED from the real corpus (``derive_role_constraints``), not hardcoded to
Smith's.  Any merchant corpus in the same schema yields its own constraints.

Corpus schema (per receipt): ``{"lines": {line_id_str: text}, "sections":
[{"section_type", "line_ids"}...]}``.  Candidate schema: a composed.json entry
with ``tokens`` (emitted stream) and ``metadata.synthetic_receipt_preview``
(intended line set: ``lines`` = [{"line_id", "text"}...]).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Generic role detectors (merchant-agnostic).  The *detector* is fixed; the
# per-role cardinality cap and zone are LEARNED from the corpus.  A role is a
# line-level concept: a line matches a role if its text matches the pattern.
# --------------------------------------------------------------------------
ROLE_PATTERNS: dict[str, str] = {
    "cashier": r"\bcashier\b",
    "phone": r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",
    "total_items": r"total\s+number\s+of\s+items\s+sold",
    "balance": r"\bbalance\b",
    "change": r"\bchange\b",
    "subtotal": r"\bsub\s*total\b",
}

# Fraction of a role's occurrences that must fall on one side of the first-item
# boundary for the zone to be learned as strict (else "any").  <1.0 so a single
# section-labeling outlier does not erase a real header-zone constraint.
_ZONE_DOMINANCE = 0.8

# A priced-item line: carries a money amount and is not itself a summary/role
# line.  Used to locate the "first item" boundary that defines the header zone.
_MONEY_RE = re.compile(r"(?<!\d)\d{1,4}\.\d{2}(?!\d)")
_SUMMARY_WORDS = re.compile(
    r"\b(balance|change|total|subtotal|tax|exempt|amount|amt|savings|"
    r"points|ref#|aid|tc|purchase|mastercard|visa|debit|credit|cashier)\b",
    re.I,
)


def _is_item_line(text: str) -> bool:
    """A line that looks like a priced product row (money + non-summary text)."""
    if not _MONEY_RE.search(text):
        return False
    if _SUMMARY_WORDS.search(text):
        return False
    # needs some alphabetic product text, not a bare number line
    return bool(re.search(r"[A-Za-z]{2,}", text))


# --------------------------------------------------------------------------
# (a) derive role constraints from the real corpus
# --------------------------------------------------------------------------
@dataclass
class RoleConstraint:
    role: str
    max_count: int  # cardinality cap: max occurrences observed per receipt
    zone: str  # "before_first_item" | "after_first_item" | "any"
    n_receipts_present: int
    support: int  # total receipts scanned


def _first_item_line_id(lines: dict[str, str], sections: list[dict]) -> int | None:
    """First item boundary: prefer the ITEMS section min line_id; fall back to
    the first line whose text looks like a priced item."""
    for s in sections or ():
        if str(s.get("section_type")) == "ITEMS" and s.get("line_ids"):
            return min(int(x) for x in s["line_ids"])
    cand = [int(lid) for lid, t in lines.items() if _is_item_line(t)]
    return min(cand) if cand else None


def derive_role_constraints(corpus: list[dict]) -> dict[str, RoleConstraint]:
    """Learn per-role cardinality + zone from the real corpus.

    For each role, the cardinality cap is the *max* occurrences seen in any one
    real receipt, and the zone is inferred from where every occurrence sits
    relative to that receipt's first item line.
    """
    compiled = {r: re.compile(p, re.I) for r, p in ROLE_PATTERNS.items()}
    per_role_counts: dict[str, list[int]] = {r: [] for r in ROLE_PATTERNS}
    per_role_before: dict[str, int] = {r: 0 for r in ROLE_PATTERNS}
    per_role_after: dict[str, int] = {r: 0 for r in ROLE_PATTERNS}
    support = 0

    for rec in corpus:
        lines = {str(k): v for k, v in (rec.get("lines") or {}).items()}
        if not lines:
            continue
        support += 1
        first_item = _first_item_line_id(lines, rec.get("sections") or [])
        for role, rx in compiled.items():
            hits = [int(lid) for lid, t in lines.items() if rx.search(str(t))]
            per_role_counts[role].append(len(hits))
            if first_item is not None:
                for lid in hits:
                    if lid < first_item:
                        per_role_before[role] += 1
                    else:
                        per_role_after[role] += 1

    out: dict[str, RoleConstraint] = {}
    for role in ROLE_PATTERNS:
        counts = per_role_counts[role]
        present = sum(1 for c in counts if c > 0)
        max_count = max(counts) if counts else 0
        before, after = per_role_before[role], per_role_after[role]
        # Dominant-proportion vote: robust to a lone section-labeling outlier
        # (e.g. one receipt whose ITEMS section min line_id is mislabeled).
        total = before + after
        if total and before / total >= _ZONE_DOMINANCE:
            zone = "before_first_item"
        elif total and after / total >= _ZONE_DOMINANCE:
            zone = "after_first_item"
        else:
            zone = "any"
        out[role] = RoleConstraint(
            role=role,
            max_count=max_count,
            zone=zone,
            n_receipts_present=present,
            support=support,
        )
    return out


def constraints_to_json(constraints: dict[str, RoleConstraint]) -> dict:
    return {
        r: {
            "max_count": c.max_count,
            "zone": c.zone,
            "n_receipts_present": c.n_receipts_present,
            "support": c.support,
        }
        for r, c in constraints.items()
    }


# --------------------------------------------------------------------------
# candidate line reconstruction
# --------------------------------------------------------------------------
@dataclass
class Line:
    line_id: int
    text: str
    order: int  # reading-order index within the candidate


def preview_lines(candidate: dict) -> list[Line]:
    """The composer's intended line set (metadata.synthetic_receipt_preview)."""
    prev = (candidate.get("metadata") or {}).get("synthetic_receipt_preview") or {}
    out = []
    for i, ln in enumerate(prev.get("lines") or []):
        if isinstance(ln, dict):
            out.append(Line(int(ln.get("line_id", i)), str(ln.get("text", "")), i))
        else:
            out.append(Line(i, str(ln), i))
    return out


# --------------------------------------------------------------------------
# (a) check + repair role cardinality / zone on a candidate
# --------------------------------------------------------------------------
@dataclass
class Violation:
    role: str
    kind: str  # "cardinality" | "zone"
    detail: str
    offending_line_ids: list[int]


def check_role_constraints(
    candidate: dict, constraints: dict[str, RoleConstraint]
) -> list[Violation]:
    lines = preview_lines(candidate)
    compiled = {r: re.compile(p, re.I) for r, p in ROLE_PATTERNS.items()}
    # first item boundary in the candidate (reading order)
    first_item_order = next(
        (ln.order for ln in lines if _is_item_line(ln.text)), None
    )
    violations: list[Violation] = []
    for role, con in constraints.items():
        if con.n_receipts_present == 0:
            continue  # role never seen in corpus -> nothing learned, skip
        rx = compiled[role]
        hits = [ln for ln in lines if rx.search(ln.text)]
        if con.max_count and len(hits) > con.max_count:
            violations.append(
                Violation(
                    role,
                    "cardinality",
                    f"{len(hits)} occurrences > learned cap {con.max_count}",
                    [h.line_id for h in hits],
                )
            )
        if con.zone == "before_first_item" and first_item_order is not None:
            bad = [h for h in hits if h.order >= first_item_order]
            if bad:
                violations.append(
                    Violation(
                        role,
                        "zone",
                        f"{len(bad)} occurrence(s) sit at/after first item "
                        f"(zone={con.zone})",
                        [h.line_id for h in bad],
                    )
                )
    return violations


def repair_role_constraints(
    candidate: dict, constraints: dict[str, RoleConstraint]
) -> tuple[dict, list[dict]]:
    """Return (repaired_candidate, actions).

    For a ``before_first_item`` singleton role that is duplicated, keep the
    first in-zone occurrence and drop every out-of-zone / surplus occurrence
    (line removed from both preview and emitted tokens).
    """
    import copy

    cand = copy.deepcopy(candidate)
    lines = preview_lines(cand)
    compiled = {r: re.compile(p, re.I) for r, p in ROLE_PATTERNS.items()}
    first_item_order = next(
        (ln.order for ln in lines if _is_item_line(ln.text)), None
    )
    drop_line_ids: set[int] = set()
    actions: list[dict] = []
    for role, con in constraints.items():
        if con.n_receipts_present == 0 or con.zone != "before_first_item":
            continue
        rx = compiled[role]
        hits = [ln for ln in lines if rx.search(ln.text)]
        if len(hits) <= con.max_count:
            continue
        # keep the first in-zone occurrence, drop the rest
        in_zone = [
            h
            for h in hits
            if first_item_order is None or h.order < first_item_order
        ]
        keep = in_zone[0] if in_zone else hits[0]
        for h in hits:
            if h is keep:
                continue
            drop_line_ids.add(h.line_id)
            actions.append(
                {
                    "role": role,
                    "action": "drop_duplicate_line",
                    "line_id": h.line_id,
                    "text": h.text,
                    "reason": "surplus singleton / out-of-zone role line",
                }
            )
    if drop_line_ids:
        _drop_preview_lines(cand, drop_line_ids)
        _drop_tokens_matching(cand, [a["text"] for a in actions])
    return cand, actions


def _drop_preview_lines(candidate: dict, line_ids: set[int]) -> None:
    prev = (candidate.get("metadata") or {}).get("synthetic_receipt_preview")
    if not prev:
        return
    prev["lines"] = [
        ln
        for ln in prev.get("lines") or []
        if not (isinstance(ln, dict) and int(ln.get("line_id", -1)) in line_ids)
    ]
    prev["line_count"] = len(prev["lines"])


def _drop_tokens_matching(candidate: dict, line_texts: list[str]) -> None:
    """Remove the whitespace-split tokens of each dropped line from the emitted
    token stream (and the parallel bboxes / ner_tags), once each."""
    drop_multiset: dict[str, int] = {}
    for text in line_texts:
        for tok in str(text).split():
            drop_multiset[tok] = drop_multiset.get(tok, 0) + 1
    toks = candidate.get("tokens") or []
    bboxes = candidate.get("bboxes")
    ner = candidate.get("ner_tags")
    keep_idx = []
    for i, tok in enumerate(toks):
        if drop_multiset.get(tok, 0) > 0:
            drop_multiset[tok] -= 1
            continue
        keep_idx.append(i)
    candidate["tokens"] = [toks[i] for i in keep_idx]
    if isinstance(bboxes, list) and len(bboxes) == len(toks):
        candidate["bboxes"] = [bboxes[i] for i in keep_idx]
    if isinstance(ner, list) and len(ner) == len(toks):
        candidate["ner_tags"] = [ner[i] for i in keep_idx]


# --------------------------------------------------------------------------
# (b) scaffold-artifact scrub
# --------------------------------------------------------------------------
# Detectors for auth/card artifacts that must never be copied verbatim.
_AID_RE = re.compile(r"^A?0{2,}\d{6,}$")  # EMV AID e.g. A0000000041010 / 0000000041010
_TC_RE = re.compile(r"^[0-9A-F]{16}$")  # transaction cryptogram, 16 hex
_MASKED_PAN_RE = re.compile(r"^(\*{2,})(\d{2,6})$")  # ****2777 / *******0658
_REF_RE = re.compile(r"^\d{5,7}$")  # REF# body (contextual, see below)
_PLAIN_PAN_RE = re.compile(r"^\d{13,19}$")  # long bare card-ish number


@dataclass
class ScrubReport:
    replacements: list[dict] = field(default_factory=list)


def _rng_for(seed_key: str):
    import random

    h = hashlib.sha256(seed_key.encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _synth_hex(rng, n: int, avoid: set[str]) -> str:
    for _ in range(64):
        v = "".join(rng.choice("0123456789ABCDEF") for _ in range(n))
        if v not in avoid:
            return v
    return v


def _synth_digits(rng, n: int, avoid: set[str]) -> str:
    for _ in range(64):
        v = "".join(rng.choice("0123456789") for _ in range(n))
        if v not in avoid:
            return v
    return v


def _real_artifact_values(corpus: list[dict]) -> set[str]:
    """Every token in the real corpus that looks like an auth/card artifact --
    the exact values a synthetic receipt must never reproduce."""
    vals: set[str] = set()
    for rec in corpus:
        for t in (rec.get("lines") or {}).values():
            for tok in re.split(r"\s+", str(t)):
                tok = tok.strip(".,:;")
                if (
                    _TC_RE.match(tok)
                    or _AID_RE.match(tok)
                    or _MASKED_PAN_RE.match(tok)
                    or _PLAIN_PAN_RE.match(tok)
                ):
                    vals.add(tok)
    return vals


def scrub_scaffold_artifacts(
    candidate: dict, real_values: set[str]
) -> tuple[dict, ScrubReport]:
    """Regenerate card/REF#/AID/TC/auth tokens synthetically (never verbatim).

    Deterministic per-candidate (seeded by candidate_id) so re-runs are stable.
    A token is scrubbed when it (i) matches an auth artifact pattern AND
    (ii) is copied verbatim from the real corpus, or is a masked PAN / TC /
    AID by shape.  Replacements are guaranteed not to equal any real value.
    """
    import copy

    cand = copy.deepcopy(candidate)
    rng = _rng_for(str(cand.get("candidate_id", "seed")))
    report = ScrubReport()
    toks = cand.get("tokens") or []
    new_toks = list(toks)

    # REF# only when the preceding token signals a reference field
    ref_positions: set[int] = set()
    for i, tok in enumerate(toks):
        if re.match(r"REF#?:?", str(tok), re.I) and i + 1 < len(toks):
            ref_positions.add(i + 1)

    def _replace(i: int, new: str, kind: str, old: str) -> None:
        new_toks[i] = new
        report.replacements.append(
            {"index": i, "kind": kind, "old": old, "new": new}
        )

    for i, tok in enumerate(toks):
        tok = str(tok)
        m = _MASKED_PAN_RE.match(tok)
        if m:
            mask, tail = m.group(1), m.group(2)
            new = mask + _synth_digits(rng, len(tail), real_values)
            _replace(i, new, "masked_pan", tok)
            continue
        if _TC_RE.match(tok):
            _replace(i, _synth_hex(rng, 16, real_values), "tc", tok)
            continue
        if _AID_RE.match(tok):
            # keep the leading 'A' + zero run shape; randomize the tail
            prefix = "A" if tok.startswith("A") else ""
            zeros = re.match(r"A?(0*)", tok).group(1)
            tail_len = len(tok) - len(prefix) - len(zeros)
            new = prefix + zeros + _synth_digits(rng, max(1, tail_len), real_values)
            if new == tok or new in real_values:
                new = prefix + zeros + _synth_digits(rng, max(1, tail_len), real_values | {tok})
            _replace(i, new, "aid", tok)
            continue
        if i in ref_positions and _REF_RE.match(tok):
            _replace(i, _synth_digits(rng, len(tok), real_values), "ref", tok)
            continue
        if _PLAIN_PAN_RE.match(tok) and tok in real_values:
            _replace(i, _synth_digits(rng, len(tok), real_values), "plain_pan", tok)
            continue

    cand["tokens"] = new_toks
    return cand, report


# --------------------------------------------------------------------------
# (c) compose->render reconciliation + tax liveness
# --------------------------------------------------------------------------
@dataclass
class ReconcileReport:
    dropped_lines: list[dict] = field(default_factory=list)  # in preview, not emitted
    duplicated_tokens: list[dict] = field(default_factory=list)
    tax_issues: list[dict] = field(default_factory=list)
    ok: bool = True


def _token_multiset(tokens: list[str]) -> dict[str, int]:
    ms: dict[str, int] = {}
    for t in tokens:
        ms[str(t)] = ms.get(str(t), 0) + 1
    return ms


def reconcile(candidate: dict) -> ReconcileReport:
    """Every composed (preview) token must render exactly once in the emitted
    token stream: report dropped preview lines and any emitted token that
    appears more times than the preview intended.  Also validate tax liveness.
    """
    rep = ReconcileReport()
    prev = (candidate.get("metadata") or {}).get("synthetic_receipt_preview") or {}
    emitted = _token_multiset(candidate.get("tokens") or [])

    # preview token multiset
    prev_ms: dict[str, int] = {}
    prev_line_tokens: list[tuple[dict, list[str]]] = []
    for ln in prev.get("lines") or []:
        text = ln.get("text", "") if isinstance(ln, dict) else str(ln)
        line_toks = str(text).split()
        prev_line_tokens.append((ln if isinstance(ln, dict) else {"text": text}, line_toks))
        for t in line_toks:
            prev_ms[t] = prev_ms.get(t, 0) + 1

    truncated = bool(prev.get("truncated"))

    # dropped: preview lines whose tokens are wholly absent from emitted
    for ln, line_toks in prev_line_tokens:
        if not line_toks:
            continue
        if all(emitted.get(t, 0) == 0 for t in line_toks):
            rep.dropped_lines.append(
                {"line_id": ln.get("line_id"), "text": ln.get("text")}
            )

    # double-stitched: token emitted more often than the (untruncated) preview
    # asked for.  Skip when the preview was truncated (its counts are a floor).
    if not truncated:
        for tok, n_emit in emitted.items():
            n_prev = prev_ms.get(tok, 0)
            if n_prev and n_emit > n_prev:
                rep.duplicated_tokens.append(
                    {"token": tok, "emitted": n_emit, "preview": n_prev}
                )

    rep.tax_issues = check_tax_liveness(candidate)
    rep.ok = not (rep.dropped_lines or rep.duplicated_tokens or rep.tax_issues)
    return rep


def check_tax_liveness(candidate: dict) -> list[dict]:
    """Tax blocks must be arithmetically live, not stale-copied.

    Flags a stale TAX / TAX EXEMPTION / EXEMPTED SALES AMT block whose printed
    gross figure does not match the composed basket -- e.g. an inherited 5.05
    gross on a non-taxable 6.99 basket whose reconciliation says new_tax=0.00.
    """
    meta = candidate.get("metadata") or {}
    arith = meta.get("arithmetic_reconciliation") or {}
    prev = meta.get("synthetic_receipt_preview") or {}
    text = " ".join(
        (ln.get("text", "") if isinstance(ln, dict) else str(ln))
        for ln in prev.get("lines") or []
    )
    issues: list[dict] = []

    new_tax = _money(arith.get("new_tax"))
    basis = str(arith.get("tax_basis", ""))
    grand = _money(arith.get("new_grand_total"))

    # printed gross TAX figures in the preview
    printed = []
    for m in re.finditer(r"\bTAX(?:\s+EXEMPTION)?\s+(\d+\.\d{2})", text, re.I):
        printed.append(float(m.group(1)))
    exempted = [
        float(m.group(1))
        for m in re.finditer(r"EXEMPTED\s+SALES\s+AMT[^\d]*(\d+\.\d{2})", text, re.I)
    ]

    has_exemption_block = bool(re.search(r"\bEXEMPT", text, re.I))
    has_marketplace_ctx = bool(
        re.search(r"DOORDASH|MARKETPLA|INSTACART", text, re.I)
    )

    if new_tax is not None:
        for g in printed:
            if g > 0 and abs(g - new_tax) > 0.005:
                issues.append(
                    {
                        "kind": "stale_gross_tax",
                        "printed_gross": g,
                        "reconciled_tax": new_tax,
                        "detail": "printed TAX gross does not match composed basket",
                    }
                )
                break

    # non-taxable basket carrying an exemption block with no marketplace context
    if (
        ("non_taxable" in basis or (new_tax == 0.0))
        and has_exemption_block
        and not has_marketplace_ctx
    ):
        issues.append(
            {
                "kind": "orphan_exemption_block",
                "detail": "EXEMPTED/EXEMPTION block on a non-taxable basket with "
                "no marketplace (DOORDASH) context -- should be stripped",
                "exempted_amt": exempted[0] if exempted else None,
                "grand_total": grand,
            }
        )
    return issues


def _money(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(str(v).replace("$", "").replace(",", "").rstrip("-")), 2)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# top-level driver: run the full pass over one candidate
# --------------------------------------------------------------------------
@dataclass
class CandidateResult:
    candidate_id: str
    role_violations: list[Violation]
    role_actions: list[dict]
    scrub: ScrubReport
    reconcile: ReconcileReport
    rejected: bool  # would v1 (pre-repair) be rejected by the gate?

    def to_json(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "rejected_v1": self.rejected,
            "role_violations": [
                {
                    "role": v.role,
                    "kind": v.kind,
                    "detail": v.detail,
                    "line_ids": v.offending_line_ids,
                }
                for v in self.role_violations
            ],
            "role_repair_actions": self.role_actions,
            "scrub_replacements": self.scrub.replacements,
            "reconcile": {
                "ok": self.reconcile.ok,
                "dropped_lines": self.reconcile.dropped_lines,
                "duplicated_tokens": self.reconcile.duplicated_tokens,
                "tax_issues": self.reconcile.tax_issues,
            },
        }


def process_candidate(
    candidate: dict,
    constraints: dict[str, RoleConstraint],
    real_values: set[str],
) -> tuple[dict, CandidateResult]:
    """Full provenance-consistency pass: check + repair + scrub + reconcile.

    Returns (repaired_candidate, result).
    """
    violations = check_role_constraints(candidate, constraints)
    repaired, actions = repair_role_constraints(candidate, constraints)
    # Reconcile BEFORE scrub: the scrub substitutes token *values* (card/AID/TC)
    # without changing token count or position, so reconciling the post-scrub
    # stream against the un-scrubbed preview would report phantom drops.  The
    # structural compose->render relationship is what reconcile measures.
    recon = reconcile(repaired)
    repaired, scrub = scrub_scaffold_artifacts(repaired, real_values)
    result = CandidateResult(
        candidate_id=str(candidate.get("candidate_id", "")),
        role_violations=violations,
        role_actions=actions,
        scrub=scrub,
        reconcile=recon,
        rejected=bool(violations),
    )
    return repaired, result

"""Preserve discarded merchant-profile curator comments as PROPOSED records.

The v1 MerchantTruth migration classified every ``merchant_profiles.json``
``_comment`` / ``_face_source_comment`` leaf as ``discarded-with-reason``
(see ``merchant_truth_v1.classify_leaf``). That crosswalk disposition is
correct for a *parity* mint, but it throws away rich curator knowledge -
including the original Costco ``SELF-CHECKOUT`` observation. Contract §7.8
requires that knowledge to survive into the truth system as PROPOSED
records rather than living only in git history.

This module holds the pure, git-free logic so it is unit-testable with
injected file content; the ``scripts/import_crosswalk_notes.py`` CLI wires
it to a live ``git show`` read and the governed ``add_proposal`` accessor.

Design notes
------------
* **Every extracted leaf is written.** Zero-text-loss means every leaf's
  verbatim text persists in a written record, so preservation never depends
  on a dedupe heuristic. A multi-topic note (e.g. the Costco ``_comment``,
  which spans font, condense, footer *and* the self-checkout observation) is
  never dropped because it happens to touch a topic another proposal covers.
* **Relatedness is a non-suppressing annotation.** When an existing proposal
  is textually related to a leaf, the written record carries a ``related_to``
  field in its claim envelope naming that proposal's ``claim_slug`` - a
  cross-reference, not a substitute. The relatedness signal is deliberately
  conservative (a shared distinctive hyphenated compound like
  ``self-checkout``, or a shared contiguous >=``MIN_COVERAGE_NGRAM`` token
  phrase) so common bigrams such as ``font size`` do not trip it; but even a
  false relatedness link is low-harm now, because it only adds a reference.
* **Provenance lives in the claim body.** ``MerchantTruthProposal`` has no
  provenance field, so each imported claim is a canonical JSON envelope
  ``{"provenance": {...}, "text": <verbatim>}`` (plus ``related_to`` when
  set). ``text`` is the byte-exact curator note.
* **claim_slug is derived from the leaf path** (stable + deduplicatable), so
  an idempotent re-run recognises an already-imported leaf and skips it - the
  only non-writing disposition, and only because the text already persists in
  the prior record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from receipt_dynamo.entities.merchant_truth import MerchantTruthProposal
from receipt_dynamo.migrations.merchant_truth_v1 import slugify_merchant

# Leaf field names the v1 crosswalk discarded as documentation-only.
COMMENT_FIELDS = frozenset({"_comment", "_face_source_comment"})

# The system-of-record that authored these claims (contract §7.8 provenance).
PROVENANCE_SOURCE = "profile-comment"

# Minimum contiguous shared token phrase length that signals relatedness.
# Bigrams over-trigger on boilerplate (e.g. "font size"); require three.
MIN_COVERAGE_NGRAM = 3

# A distinctive hyphenated compound: two or more alphabetic sub-words joined
# by hyphens (e.g. "self-checkout", "variant-aware"). These are specific
# enough to signal relatedness on their own.
_HYPHEN_COMPOUND = re.compile(r"[a-z]+(?:-[a-z]+)+")


@dataclass(frozen=True)
class ExtractedLeaf:
    """One discarded curator-comment leaf, captured verbatim."""

    merchant_name: str
    slug: str
    leaf_path: str  # full source path, e.g. profiles.Costco Wholesale._comment
    rel_path: str  # merchant-relative path, e.g. layout_template._comment
    text: str
    claim_slug: str


@dataclass(frozen=True)
class LeafDecision:
    """Per-leaf disposition in an import plan.

    ``WRITE`` persists a new PROPOSED record (optionally annotated with
    ``related_to``); ``SKIP_EXISTS`` is the idempotent no-op for a leaf whose
    ``claim_slug`` is already present (its text already persists). There is no
    suppressing disposition - a related leaf still writes.
    """

    leaf: ExtractedLeaf
    action: str  # WRITE | SKIP_EXISTS
    related_to: str | None = None  # existing claim_slug this leaf references
    matched_phrase: str | None = None  # the shared compound / phrase
    proposal: MerchantTruthProposal | None = None  # set when action == WRITE


@dataclass(frozen=True)
class LeafVerification:
    """Persistence result for one extracted leaf."""

    leaf_path: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ImportPlan:
    """The full set of per-leaf decisions for one run."""

    decisions: list[LeafDecision] = field(default_factory=list)

    @property
    def to_write(self) -> list[LeafDecision]:
        return [d for d in self.decisions if d.action == "WRITE"]

    @property
    def related(self) -> list[LeafDecision]:
        return [d for d in self.decisions if d.related_to is not None]

    @property
    def skipped(self) -> list[LeafDecision]:
        return [d for d in self.decisions if d.action == "SKIP_EXISTS"]


def _walk(value: Any, prefix: tuple[str, ...]) -> Iterable[tuple[str, Any]]:
    """Yield (dotted-path, leaf-value) for every scalar leaf under ``value``."""
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk(value[key], (*prefix, key))
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk(item, (*prefix, "[]"))
        return
    yield (".".join(prefix), value)


def derive_claim_slug(rel_path: str) -> str:
    """Derive a stable, deduplicatable claim_slug from a leaf's relative path.

    ``_comment`` (a merchant's top-level note) becomes ``profile-comment``;
    nested notes keep their path, e.g. ``layout_template._comment`` becomes
    ``layout-template-comment``. The mapping is deterministic so re-runs
    recompute the identical claim_slug and dedupe against a prior import.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive claim_slug from {rel_path!r}")
    if slug == "comment":
        return "profile-comment"
    return slug


def extract_comment_leaves(document: dict[str, Any]) -> list[ExtractedLeaf]:
    """Walk every ``*_comment`` leaf under each merchant profile, verbatim.

    Only merchant-owned leaves are captured: registry-level documentation
    (top-level ``_comment`` / ``_section_scale_note``) is out of scope.
    """
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("profile document must contain a profiles map")
    leaves: list[ExtractedLeaf] = []
    for merchant_name in sorted(profiles):
        slug = slugify_merchant(merchant_name)
        subtree = profiles[merchant_name]
        for rel_path, value in _walk(subtree, ()):
            segments = rel_path.split(".")
            if not (COMMENT_FIELDS & set(segments)):
                continue
            if not isinstance(value, str):
                raise ValueError(
                    f"non-string comment leaf at "
                    f"profiles.{merchant_name}.{rel_path}"
                )
            leaves.append(
                ExtractedLeaf(
                    merchant_name=merchant_name,
                    slug=slug,
                    leaf_path=f"profiles.{merchant_name}.{rel_path}",
                    rel_path=rel_path,
                    text=value,
                    claim_slug=derive_claim_slug(rel_path),
                )
            )
    return leaves


def build_claim(
    text: str,
    leaf_path: str,
    git_sha: str,
    *,
    related_to: str | None = None,
) -> str:
    """Build the canonical JSON claim envelope carrying text + provenance.

    ``related_to`` (when set) cross-references an existing proposal's
    ``claim_slug``; it never replaces ``text``, which stays byte-exact.
    """
    envelope: dict[str, Any] = {
        "provenance": {
            "source": PROVENANCE_SOURCE,
            "leaf_path": leaf_path,
            "git_sha": git_sha,
        },
        "text": text,
    }
    if related_to is not None:
        envelope["related_to"] = related_to
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True)


def extract_text_from_claim(claim: str) -> str:
    """Return the verbatim ``text`` embedded in a claim envelope."""
    return json.loads(claim)["text"]


def _proposal_text(proposal: MerchantTruthProposal) -> str:
    """Return an existing proposal's human text.

    Crosswalk imports store a JSON envelope; owner proposals store plain
    prose. Compare against the envelope's ``text`` when present.
    """
    claim = proposal.claim
    try:
        data = json.loads(claim)
    except (ValueError, TypeError):
        return claim
    if isinstance(data, dict) and isinstance(data.get("text"), str):
        return data["text"]
    return claim


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase and split on non-alphanumerics into content tokens."""
    return [tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok]


def _compounds(text: str) -> set[str]:
    """Distinctive hyphenated compound terms present in ``text``."""
    return set(_HYPHEN_COMPOUND.findall(text.lower()))


def _longest_shared_phrase(left: str, right: str, min_len: int) -> str | None:
    """Longest contiguous shared token run of >= ``min_len`` tokens, or None."""
    left_tokens = _normalize_tokens(left)
    right_tokens = _normalize_tokens(right)
    upper = min(len(left_tokens), len(right_tokens))
    for size in range(upper, min_len - 1, -1):
        right_grams = {
            tuple(right_tokens[i : i + size])
            for i in range(len(right_tokens) - size + 1)
        }
        for start in range(len(left_tokens) - size + 1):
            candidate = tuple(left_tokens[start : start + size])
            if candidate in right_grams:
                return " ".join(candidate)
    return None


def find_related(
    leaf: ExtractedLeaf,
    existing: list[MerchantTruthProposal],
) -> tuple[str, str] | None:
    """Return (existing_claim_slug, matched_term) if a proposal relates to leaf.

    Relatedness is a conservative textual signal against the existing
    proposal's text: a shared distinctive hyphenated compound (preferred, e.g.
    ``self-checkout``) or, failing that, a shared contiguous
    >=``MIN_COVERAGE_NGRAM`` token phrase. It only annotates the written
    record; it never suppresses a write.
    """
    leaf_compounds = _compounds(leaf.text)
    best: tuple[str, str] | None = None
    best_score = 0
    for proposal in existing:
        ptext = _proposal_text(proposal)
        shared = leaf_compounds & _compounds(ptext)
        if shared:
            term = max(sorted(shared), key=len)
            score = 1000 + len(term)  # compounds beat any phrase
            if score > best_score:
                best, best_score = (proposal.claim_slug, term), score
            continue
        phrase = _longest_shared_phrase(leaf.text, ptext, MIN_COVERAGE_NGRAM)
        if phrase:
            score = len(phrase.split())
            if score > best_score:
                best, best_score = (proposal.claim_slug, phrase), score
    return best


def plan_import(
    leaves: list[ExtractedLeaf],
    existing_by_slug: dict[str, list[MerchantTruthProposal]],
    *,
    git_sha: str,
    created_at: str,
) -> ImportPlan:
    """Decide per leaf: WRITE a new proposal (annotated with ``related_to``
    when an existing proposal is related), or SKIP an idempotent re-run.

    Every extracted leaf gets a persisting disposition - a related leaf still
    writes its full record - so no curator note is ever dropped.
    """
    decisions: list[LeafDecision] = []
    for leaf in leaves:
        existing = existing_by_slug.get(leaf.slug, [])
        if leaf.claim_slug in {p.claim_slug for p in existing}:
            decisions.append(LeafDecision(leaf=leaf, action="SKIP_EXISTS"))
            continue
        related = find_related(leaf, existing)
        related_to = related[0] if related else None
        proposal = MerchantTruthProposal(
            slug=leaf.slug,
            created_at=created_at,
            claim_slug=leaf.claim_slug,
            claim=build_claim(
                leaf.text, leaf.leaf_path, git_sha, related_to=related_to
            ),
        )
        decisions.append(
            LeafDecision(
                leaf=leaf,
                action="WRITE",
                related_to=related_to,
                matched_phrase=related[1] if related else None,
                proposal=proposal,
            )
        )
    return ImportPlan(decisions=decisions)


def verify_persistence(
    plan: ImportPlan,
    reference_document: dict[str, Any],
    *,
    scope: set[str] | None = None,
) -> list[LeafVerification]:
    """Assert every in-scope extracted leaf's text persists in a record.

    Extraction-driven (not write-driven): re-extract the leaves from a fresh
    ``git show`` parse of the source and require, for each, a plan decision
    whose persisted text is byte-identical. A leaf with no decision is
    ``planless`` and fails - which makes the old "covered-but-unwritten"
    suppression class structurally impossible: a dropped leaf is a red row,
    not a silent omission.

    ``scope`` (a set of ``leaf_path``) restricts the assertion to the leaves
    the run intended to handle - e.g. when ``--merchants`` narrowed the plan.
    Leaves outside scope were never in play and are not treated as planless.

    Honesty note: this checks *extraction-faithfulness + envelope round-trip*,
    not an OCR-of-the-database read-back. Extraction here re-uses the same
    walker as the import path, so a walker bug would affect both reads; what it
    proves is that the text captured for each source leaf survives verbatim
    into the record scheduled to persist it (or into the prior record an
    idempotent skip points at).
    """
    decisions = {d.leaf.leaf_path: d for d in plan.decisions}
    results: list[LeafVerification] = []
    for leaf in extract_comment_leaves(reference_document):
        if scope is not None and leaf.leaf_path not in scope:
            continue
        decision = decisions.get(leaf.leaf_path)
        if decision is None:
            results.append(
                LeafVerification(
                    leaf.leaf_path,
                    False,
                    "planless: no record scheduled to persist this leaf",
                )
            )
            continue
        if decision.action == "SKIP_EXISTS":
            ok = decision.leaf.text == leaf.text
            results.append(
                LeafVerification(
                    leaf.leaf_path,
                    ok,
                    (
                        "already persisted (idempotent skip)"
                        if ok
                        else "text drift vs source on an already-imported leaf"
                    ),
                )
            )
            continue
        assert decision.proposal is not None
        persisted = extract_text_from_claim(decision.proposal.claim)
        if leaf.text == decision.leaf.text == persisted:
            results.append(
                LeafVerification(
                    leaf.leaf_path, True, "persists byte-identical"
                )
            )
        else:
            results.append(
                LeafVerification(
                    leaf.leaf_path,
                    False,
                    f"text drift: {len(leaf.text)}B source vs "
                    f"{len(persisted)}B persisted",
                )
            )
    return results

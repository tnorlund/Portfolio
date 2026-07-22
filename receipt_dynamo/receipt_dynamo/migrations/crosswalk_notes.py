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
* **Provenance lives in the claim body.** ``MerchantTruthProposal`` has no
  provenance field, so each imported claim is a canonical JSON envelope
  ``{"provenance": {...}, "text": <verbatim>}``. ``text`` is the byte-exact
  curator note; the zero-text-loss check verifies it round-trips identically
  against a fresh ``git show``.
* **claim_slug is derived from the leaf path** (stable + deduplicatable), so
  an idempotent re-run recognises an already-imported leaf and skips it.
* **Textual dedupe is anchored on the *existing* proposal's claim_slug.** A
  leaf is "covered by" a prior proposal when the leaf text contains, as a
  contiguous token phrase, some >=2-token contiguous n-gram of that
  proposal's claim_slug. The curator-authored claim_slug is the semantic key
  of the observation, so this is a conservative, false-positive-resistant
  signal: the Costco ``_comment`` (which mentions ``SELF-CHECKOUT``) is
  covered by ``self-checkout-layout-variant`` and referenced rather than
  duplicated, while the neighbouring ``layout_template._comment`` /
  ``_face_source_comment`` notes are NOT falsely suppressed.
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

# Minimum contiguous claim_slug n-gram length that counts as textual coverage.
MIN_COVERAGE_NGRAM = 2


@dataclass(frozen=True)
class ExtractedLeaf:
    """One discarded curator-comment leaf, captured verbatim."""

    merchant_name: str
    slug: str
    leaf_path: (
        str  # full source path, e.g. "profiles.Costco Wholesale._comment"
    )
    rel_path: str  # merchant-relative path, e.g. "layout_template._comment"
    text: str
    claim_slug: str


@dataclass(frozen=True)
class LeafDecision:
    """Per-leaf disposition in an import plan."""

    leaf: ExtractedLeaf
    action: str  # WRITE | COVERED_BY | SKIP_EXISTS
    covered_by: str | None = None  # existing claim_slug when COVERED_BY
    matched_phrase: str | None = None  # the shared claim_slug phrase
    proposal: MerchantTruthProposal | None = None  # set when action == WRITE


@dataclass(frozen=True)
class LeafVerification:
    """Byte-identical text-loss result for one leaf."""

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
    def covered(self) -> list[LeafDecision]:
        return [d for d in self.decisions if d.action == "COVERED_BY"]

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


def build_claim(text: str, leaf_path: str, git_sha: str) -> str:
    """Build the canonical JSON claim envelope carrying text + provenance."""
    envelope = {
        "provenance": {
            "source": PROVENANCE_SOURCE,
            "leaf_path": leaf_path,
            "git_sha": git_sha,
        },
        "text": text,
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True)


def extract_text_from_claim(claim: str) -> str:
    """Return the verbatim ``text`` embedded in a claim envelope."""
    return json.loads(claim)["text"]


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase and split on non-alphanumerics into content tokens."""
    return [tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok]


def _claim_slug_ngrams(claim_slug: str) -> list[list[str]]:
    """Return every contiguous >=MIN_COVERAGE_NGRAM token run of a slug."""
    tokens = [tok for tok in claim_slug.split("-") if tok]
    ngrams: list[list[str]] = []
    for size in range(MIN_COVERAGE_NGRAM, len(tokens) + 1):
        for start in range(0, len(tokens) - size + 1):
            ngrams.append(tokens[start : start + size])
    return ngrams


def _contiguous_sublist(needle: list[str], haystack: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(0, len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False


def find_coverage(
    leaf: ExtractedLeaf,
    existing: list[MerchantTruthProposal],
) -> tuple[str, str] | None:
    """Return (existing_claim_slug, matched_phrase) if a proposal covers leaf.

    Coverage is anchored on the existing proposal's *claim_slug*: the leaf is
    covered when its text contains a contiguous >=2-token n-gram of that slug
    as a contiguous token phrase. Longer n-grams are preferred so the report
    cites the most specific shared phrase.
    """
    leaf_tokens = _normalize_tokens(leaf.text)
    best: tuple[str, str] | None = None
    best_len = 0
    for proposal in existing:
        for ngram in _claim_slug_ngrams(proposal.claim_slug):
            if len(ngram) > best_len and _contiguous_sublist(
                ngram, leaf_tokens
            ):
                best = (proposal.claim_slug, " ".join(ngram))
                best_len = len(ngram)
    return best


def plan_import(
    leaves: list[ExtractedLeaf],
    existing_by_slug: dict[str, list[MerchantTruthProposal]],
    *,
    git_sha: str,
    created_at: str,
) -> ImportPlan:
    """Decide per leaf: WRITE a new proposal, SKIP an idempotent re-run, or
    reference a prior proposal that already covers the observation."""
    decisions: list[LeafDecision] = []
    for leaf in leaves:
        existing = existing_by_slug.get(leaf.slug, [])
        existing_slugs = {p.claim_slug for p in existing}
        if leaf.claim_slug in existing_slugs:
            decisions.append(LeafDecision(leaf=leaf, action="SKIP_EXISTS"))
            continue
        coverage = find_coverage(leaf, existing)
        if coverage is not None:
            decisions.append(
                LeafDecision(
                    leaf=leaf,
                    action="COVERED_BY",
                    covered_by=coverage[0],
                    matched_phrase=coverage[1],
                )
            )
            continue
        proposal = MerchantTruthProposal(
            slug=leaf.slug,
            created_at=created_at,
            claim_slug=leaf.claim_slug,
            claim=build_claim(leaf.text, leaf.leaf_path, git_sha),
        )
        decisions.append(
            LeafDecision(leaf=leaf, action="WRITE", proposal=proposal)
        )
    return ImportPlan(decisions=decisions)


def verify_no_text_loss(
    plan: ImportPlan,
    reference_document: dict[str, Any],
) -> list[LeafVerification]:
    """Diff every extracted leaf against an independent re-read of the source.

    ``reference_document`` is a fresh parse of ``git show <sha>:...`` obtained
    separately from the extraction read. For each leaf we re-walk the
    reference document to the same path and require the value to be
    byte-identical to the captured text (and, for WRITE decisions, to the text
    embedded in the built claim envelope). Any drift is reported red.
    """
    reference = {
        leaf.leaf_path: leaf.text
        for leaf in extract_comment_leaves(reference_document)
    }
    results: list[LeafVerification] = []
    for decision in plan.decisions:
        leaf = decision.leaf
        ref_text = reference.get(leaf.leaf_path)
        if ref_text is None:
            results.append(
                LeafVerification(
                    leaf.leaf_path,
                    False,
                    "leaf absent from independent re-read",
                )
            )
            continue
        captured = leaf.text
        # For a WRITE, also confirm the claim envelope round-trips the text.
        if decision.proposal is not None:
            captured = extract_text_from_claim(decision.proposal.claim)
        if ref_text == leaf.text == captured:
            results.append(
                LeafVerification(leaf.leaf_path, True, "byte-identical")
            )
        else:
            results.append(
                LeafVerification(
                    leaf.leaf_path,
                    False,
                    f"text drift: {len(ref_text)}B ref vs "
                    f"{len(captured)}B captured",
                )
            )
    return results

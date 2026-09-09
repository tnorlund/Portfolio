"""Merchant slug fallback and identifier-aware alias resolution.

Everything here is read-only. ``resolve_line`` reaches DynamoDB only through
``DynamoClient.get_product_alias`` and never writes an alias, so a lookup can
run any number of times without minting revisions.

Resolution order (SPRINT_2 §2 MS), so an identifier alias can never bypass a
person's decision:

1. Derive the keys for the line: ``ITEM#<identifier>`` when the line carries
   a retailer item number (Costco slugs only), Target DPCI, or UPC, and
   ``TEXT#<normalized>`` with the Costco ``E `` flag and the identifier
   stripped. When stripping changed the text, the full-line
   ``TEXT#<normalize_product_text(line)>`` key is read as well, so a decision
   recorded under the unstripped text (the line-item GSI1 form) still counts.
2. Read both aliases and drop the inapplicable ones first: a size-scoped
   alias whose ``size`` disagrees with the line's size evidence is ignored,
   and an automatic (non-user) alias whose ``expires_at`` has passed is
   treated as absent. User decisions never expire.
3. Among applicable aliases, any with method ``user`` decides. Two user
   decisions that disagree leave the line ``pending`` with reason
   ``user_conflict`` and both rows named in ``alias_refs``.
4. Only then: the automatic ``ITEM`` alias, then the automatic ``TEXT`` alias.
5. Nothing applicable: ``pending`` when an expired automatic row exists (the
   SPEC retries expired decisions on the existing revision), otherwise
   ``unaliased``.

Size applicability compares strings (whitespace-collapsed, case-folded) and
never converts units: ``"16 oz"`` and ``"16 fl oz"`` are different sizes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from receipt_dynamo.entities.merchant_truth import MerchantTruthActive
from receipt_dynamo.entities.nutrition_support import read_nutrition_json
from receipt_dynamo.entities.product_alias import ProductAlias
from receipt_dynamo.entities.receipt_line_item import (
    normalize_product_text,
    slugify_merchant,
)
from receipt_dynamo.merchant_truth_loader import (
    build_fleet_alias_map,
    normalize_merchant_alias,
)

ResolutionStatus = Literal[
    "matched", "pending", "no_match", "not_food", "rejected", "unaliased"
]
DecidedBy = Literal["user", "automatic"]

# Costco prints an optional ``E `` (EBT/food) flag, then a 4-7 digit warehouse
# item number, then the description (or nothing, when OCR lost it). Only a
# leading number is an identifier: a trailing number is a size or count. This
# rule applies to Costco slugs only; "1000 ISLAND DRESSING" elsewhere is text.
_COSTCO_ITEM = re.compile(r"^(?:E\s+)?(\d{4,7})(?=\s|$)")
_COSTCO_SLUGS = re.compile(r"^costco(?:-|$)")
# Target DPCI: department-class-item, printed with dashes on every line.
_DPCI = re.compile(r"(?<![\w-])(\d{3}-\d{2}-\d{4})(?![\w-])")
# UPC-A / EAN-13 as printed (Vons, Home Depot): 11-13 digits, leading zeros
# kept, because the retailer's own bridge decides how to pad them.
_UPC = re.compile(r"(?<![\w-])(\d{11,13})(?![\w-])")
_E_FLAG = re.compile(r"^E\s+(?=\S)")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class AliasKeys:
    """The alias key texts derived from one line, before percent-escaping."""

    item_key: str | None
    text_key: str
    legacy_text_key: str | None = None
    """Full-line TEXT key when it differs from ``text_key``; read second."""

    @property
    def lookups(self) -> list[tuple[str, str]]:
        """(kind, text) pairs in resolution order: ITEM, TEXT, legacy TEXT."""
        pairs: list[tuple[str, str]] = []
        if self.item_key is not None:
            pairs.append(("ITEM", self.item_key))
        pairs.append(("TEXT", self.text_key))
        if self.legacy_text_key is not None:
            pairs.append(("TEXT", self.legacy_text_key))
        return pairs


@dataclass(frozen=True)
class AliasRef:
    kind: str
    text: str
    revision: int


@dataclass(frozen=True)
class LineResolution:
    status: ResolutionStatus
    reason: str
    keys: AliasKeys
    product_id: str | None = None
    product_revision: str | None = None
    decided_by: DecidedBy | None = None
    alias_refs: list[AliasRef] = field(default_factory=list)


class AliasReader(Protocol):
    """The only DynamoClient surface resolution uses."""

    def get_product_alias(
        self, merchant_slug: str, kind: str, text: str
    ) -> ProductAlias | None: ...


# --- merchant slug -----------------------------------------------------------


def resolve_merchant_slug(
    raw_merchant: str | None,
    *,
    fleet_alias_map: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a raw merchant name to the hyphen line-item slug.

    ``normalize_merchant_alias(raw)`` → ``fleet_alias_map`` → canonical slug →
    ``slugify_merchant``. Without a map entry the raw name is slugified
    directly, so ``"COSTCO"`` only becomes ``"costco-wholesale"`` when the map
    (``build_fleet_alias_map`` / ``load_fleet_alias_map``) carries that
    alias. This is the line-item hyphen slug, not the ReceiptPlace GSI1 slug
    (upper case, underscores).
    """
    if raw_merchant is None:
        return None
    normalized = normalize_merchant_alias(raw_merchant)
    if not normalized:
        return None
    if fleet_alias_map:
        canonical = fleet_alias_map.get(normalized)
        if canonical:
            return slugify_merchant(canonical)
    return slugify_merchant(raw_merchant)


def fleet_cache_dir() -> Path:
    """The merchant-truth cache directory shared with the render scripts."""
    return Path(
        os.environ.get("MERCHANT_TRUTH_CACHE_DIR")
        or os.path.expanduser("~/.cache/merchant_truth")
    )


def load_fleet_alias_map(
    cache_dir: Path | None = None,
) -> dict[str, str] | None:
    """Build the alias map from the offline fleet cache; never reaches AWS.

    Reads ``<cache_dir>/fleet-active.json`` (written by
    ``MerchantTruthLoader`` after an online load). Returns ``None`` when the
    cache is missing so callers fall back to ``slugify_merchant``.
    """
    fleet_path = (cache_dir or fleet_cache_dir()) / "fleet-active.json"
    if not fleet_path.exists():
        return None
    with fleet_path.open(encoding="utf-8") as handle:
        items = json.load(handle).get("items", [])
    return build_fleet_alias_map(
        [MerchantTruthActive.from_item(item) for item in items]
    )


# --- alias keys ---------------------------------------------------------------


def identifier_key_text(raw: str | None) -> str | None:
    """Canonical ``ITEM`` alias text for a printed retailer identifier.

    Convention shared with the seed script: the identifier exactly as printed,
    whitespace-trimmed, Costco ``E `` flag removed, leading zeros kept, DPCI
    dashes kept. The stored sort key is ``ITEM#<text>`` (``nutrition_key``
    leaves digits and dashes untouched). Returns ``None`` when the string is
    not one of: Costco item number (4-7 digits), Target DPCI
    (``NNN-NN-NNNN``), or UPC/EAN (11-13 digits).
    """
    if raw is None:
        return None
    candidate = _E_FLAG.sub("", raw.strip())
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", candidate):
        return candidate
    if re.fullmatch(r"\d{11,13}", candidate):
        return candidate
    if re.fullmatch(r"\d{4,7}", candidate):
        return candidate
    return None


def _find_identifier(
    line_text: str, *, merchant_slug: str
) -> tuple[str | None, str]:
    """Return (identifier text, line text with the identifier removed)."""
    stripped = line_text.strip()
    match = _DPCI.search(stripped)
    if match is None:
        match = _UPC.search(stripped)
    if match is not None:
        remainder = stripped[: match.start()] + " " + stripped[match.end() :]
        return match.group(1), remainder
    if _COSTCO_SLUGS.match(merchant_slug):
        match = _COSTCO_ITEM.match(stripped)
        if match is not None:
            return match.group(1), stripped[match.end() :]
    return None, stripped


def derive_alias_keys(line_text: str, *, merchant_slug: str) -> AliasKeys:
    """Derive the ``ITEM`` and ``TEXT`` alias key texts for one line.

    The bare leading item-number rule is Costco-only (``costco`` and
    ``costco-*`` slugs); DPCI and UPC formats are distinctive enough to apply
    at every merchant. ``legacy_text_key`` is the unstripped line text when
    stripping changed it, so decisions keyed that way are still read.
    """
    identifier, remainder = _find_identifier(
        line_text, merchant_slug=merchant_slug
    )
    remainder = _E_FLAG.sub("", remainder.strip())
    text_key = normalize_product_text(remainder)
    item_key = identifier_key_text(identifier)
    full_text = normalize_product_text(line_text) or "UNKNOWN"
    if not text_key:
        # A bare identifier line still needs a stable TEXT key so the read
        # path is uniform; nutrition_key rejects blank text.
        text_key = (
            normalize_product_text(_E_FLAG.sub("", line_text.strip()))
            or full_text
        )
    return AliasKeys(
        item_key=item_key,
        text_key=text_key,
        legacy_text_key=full_text if full_text != text_key else None,
    )


# --- resolution ---------------------------------------------------------------


def _size_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip().casefold()
    return text or None


def _size_applicable(alias: ProductAlias, size_evidence: str | None) -> bool:
    """String comparison only: sizes are never converted between units."""
    scope = read_nutrition_json(alias.applicability_json)
    scoped = _size_text(scope.get("size"))
    evidence = _size_text(size_evidence)
    if scoped is None or evidence is None:
        # Unscoped aliases apply everywhere; a line with no size evidence
        # contradicts nothing.
        return True
    return scoped == evidence


def _as_of_timestamp(as_of: date) -> int:
    if isinstance(as_of, datetime):
        moment = as_of
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return int(moment.timestamp())
    return int(
        datetime(
            as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc
        ).timestamp()
    )


def _key_name(alias: ProductAlias, keys: AliasKeys) -> str:
    if alias.kind == "TEXT" and alias.text == keys.legacy_text_key:
        return "legacy_text"
    return alias.kind.lower()


def _ref(alias: ProductAlias) -> AliasRef:
    return AliasRef(alias.kind, alias.text, alias.revision)


def _decision(
    alias: ProductAlias,
    *,
    reason: str,
    decided_by: DecidedBy,
    keys: AliasKeys,
    refs: list[AliasRef],
) -> LineResolution:
    return LineResolution(
        status=alias.status,  # type: ignore[arg-type]
        reason=reason,
        keys=keys,
        product_id=alias.product_id,
        product_revision=alias.product_revision,
        decided_by=decided_by,
        alias_refs=refs,
    )


def _same_decision(left: ProductAlias, right: ProductAlias) -> bool:
    return (left.status, left.product_id, left.product_revision) == (
        right.status,
        right.product_id,
        right.product_revision,
    )


def resolve_line(
    client: AliasReader,
    *,
    merchant_slug: str,
    line_text: str,
    size_evidence: str | None,
    as_of: date,
) -> LineResolution:
    """Resolve one receipt line against its merchant's aliases. Never writes.

    ``as_of`` is a date (midnight UTC) or an aware datetime; an automatic
    alias is expired when ``expires_at`` is at or before that instant.
    """
    keys = derive_alias_keys(line_text, merchant_slug=merchant_slug)
    now = _as_of_timestamp(as_of)

    found: list[ProductAlias] = []
    for kind, text in keys.lookups:
        alias = client.get_product_alias(merchant_slug, kind, text)
        if alias is not None:
            found.append(alias)
    refs = [_ref(alias) for alias in found]

    applicable: list[ProductAlias] = []
    expired: list[ProductAlias] = []
    for alias in found:
        if not _size_applicable(alias, size_evidence):
            continue
        if alias.method != "user" and alias.is_expired(now):
            expired.append(alias)
            continue
        applicable.append(alias)

    user_decisions = [alias for alias in applicable if alias.method == "user"]
    if len(user_decisions) > 1 and not _same_decision(*user_decisions[:2]):
        return LineResolution(
            status="pending",
            reason="user_conflict",
            keys=keys,
            alias_refs=refs,
        )
    if user_decisions:
        alias = user_decisions[0]
        return _decision(
            alias,
            reason=f"user_{_key_name(alias, keys)}",
            decided_by="user",
            keys=keys,
            refs=refs,
        )
    # ``found`` preserves lookup order: ITEM, TEXT, then legacy TEXT.
    for alias in applicable:
        return _decision(
            alias,
            reason=f"automatic_{_key_name(alias, keys)}",
            decided_by="automatic",
            keys=keys,
            refs=refs,
        )
    if expired:
        return LineResolution(
            status="pending",
            reason="expired_automatic",
            keys=keys,
            alias_refs=refs,
        )
    return LineResolution(
        status="unaliased",
        reason="inapplicable_size" if found else "no_alias",
        keys=keys,
        alias_refs=refs,
    )


__all__ = [
    "AliasKeys",
    "AliasReader",
    "AliasRef",
    "LineResolution",
    "derive_alias_keys",
    "fleet_cache_dir",
    "identifier_key_text",
    "load_fleet_alias_map",
    "resolve_line",
    "resolve_merchant_slug",
]

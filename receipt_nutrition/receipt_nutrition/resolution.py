"""Merchant slug fallback and identifier-aware alias resolution.

Everything here is read-only. ``resolve_line`` reaches DynamoDB only through
``DynamoClient.get_product_alias`` and never writes an alias, so a lookup can
run any number of times without minting revisions.

Resolution order (SPRINT_2 §2 MS), so an identifier alias can never bypass a
person's decision:

1. Derive the keys for the line: one ``ITEM#<identifier>`` per retailer item
   number (Costco slugs), DPCI (Target slugs, stored dashed), or UPC (Vons
   family: leading or trailing digit run; 11-13 digits anywhere) found, and
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
from itertools import combinations
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
# Target DPCI: department-class-item. Receipts print it undashed as a
# leading 9-digit run ("002051115 Brightroon"); the seed stores it dashed
# (ITEM#002-05-1115), so the undashed form is dashed here. The undashed rule
# is Target-only because Sprouts prints 9-digit internal codes.
_DPCI = re.compile(r"(?<![\w-])(\d{3}-\d{2}-\d{4})(?![\w-])")
_DPCI_UNDASHED = re.compile(r"(?<![\w-])(\d{3})(\d{2})(\d{4})(?![\w-])")
_TARGET_SLUGS = re.compile(r"^target(?:-|$)")
# UPC-A / EAN-13 as printed (Home Depot): 11-13 digits, leading zeros kept,
# because the retailer's own bridge decides how to pad them.
_UPC = re.compile(r"(?<![\w-])(\d{11,13})(?![\w-])")
# Vons / Albertsons family prints the UPC as a leading or trailing run of
# 4-13 digits, verbatim, no check digit ("7766117461 GNGRBRD CARAMEL S",
# "S CILANTRO ORGANIC 3338390419"). Seed convention: digits as printed.
_VONS_UPC = re.compile(r"^(\d{4,13})(?=\s|$)|(?<=\s)(\d{4,13})$")
_VONS_SLUGS = re.compile(r"^(?:vons|albertsons|safeway|pavilions)(?:-|$)")
_E_FLAG = re.compile(r"^E\s+(?=\S)")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class AliasKeys:
    """The alias key texts derived from one line, before percent-escaping."""

    item_keys: tuple[str, ...]
    """Every identifier on the line, in the order printed; all are read."""
    text_key: str
    legacy_text_key: str | None = None
    """Full-line TEXT key when it differs from ``text_key``; read last."""

    @property
    def lookups(self) -> list[tuple[str, str]]:
        """(kind, text) pairs in resolution order: ITEMs, TEXT, legacy TEXT."""
        pairs: list[tuple[str, str]] = [
            ("ITEM", text) for text in self.item_keys
        ]
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


def identifier_key_text(
    raw: str | None, *, merchant_slug: str | None = None
) -> str | None:
    """Canonical ``ITEM`` alias text for a printed retailer identifier.

    Convention shared with the seed script: the digits exactly as printed,
    whitespace-trimmed, Costco ``E `` flag removed, leading zeros kept, no
    check digit. Target DPCIs are stored dashed (``NNN-NN-NNNN``); a 9-digit
    run under a Target slug is dashed here. The stored sort key is
    ``ITEM#<text>`` (``nutrition_key`` leaves digits and dashes untouched).
    Returns ``None`` unless the string is a dashed DPCI or a 4-13 digit run.
    """
    if raw is None:
        return None
    candidate = _E_FLAG.sub("", raw.strip())
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", candidate):
        return candidate
    if (
        merchant_slug is not None
        and _TARGET_SLUGS.match(merchant_slug)
        and re.fullmatch(r"\d{9}", candidate)
    ):
        return f"{candidate[:3]}-{candidate[3:5]}-{candidate[5:]}"
    if re.fullmatch(r"\d{4,13}", candidate):
        return candidate
    return None


def _find_identifiers(
    line_text: str, *, merchant_slug: str
) -> tuple[list[str], str]:
    """Return (identifiers in printed order, line with them removed)."""
    stripped = line_text.strip()
    spans: list[tuple[int, int, str]] = []

    def claim(start: int, end: int, text: str) -> None:
        if not any(s < end and start < e for s, e, _ in spans):
            spans.append((start, end, text))

    for pattern in (_DPCI, _UPC):
        for match in pattern.finditer(stripped):
            claim(match.start(1), match.end(1), match.group(1))
    if _TARGET_SLUGS.match(merchant_slug):
        for match in _DPCI_UNDASHED.finditer(stripped):
            claim(match.start(), match.end(), "-".join(match.groups()))
    if _VONS_SLUGS.match(merchant_slug):
        for match in _VONS_UPC.finditer(stripped):
            group = 1 if match.group(1) is not None else 2
            claim(match.start(group), match.end(group), match.group(group))
    if _COSTCO_SLUGS.match(merchant_slug):
        match = _COSTCO_ITEM.match(stripped)
        if match is not None:
            claim(match.start(), match.end(), match.group(1))
    spans.sort()
    pieces: list[str] = []
    cursor = 0
    for start, end, _ in spans:
        pieces.append(stripped[cursor:start])
        cursor = end
    pieces.append(stripped[cursor:])
    return [text for _, _, text in spans], " ".join(pieces)


def derive_alias_keys(line_text: str, *, merchant_slug: str) -> AliasKeys:
    """Derive the ``ITEM`` and ``TEXT`` alias key texts for one line.

    Merchant-family rules: the bare leading 4-7 digit item number is
    Costco-only; the undashed 9-digit DPCI is Target-only (stored dashed);
    the leading-or-trailing 4-13 digit UPC run is Vons/Albertsons-only.
    Dashed DPCIs and 11-13 digit UPCs are distinctive enough to apply at
    every merchant. ``legacy_text_key`` is the unstripped line text when
    stripping changed it, so decisions keyed that way are still read.
    """
    identifiers, remainder = _find_identifiers(
        line_text, merchant_slug=merchant_slug
    )
    remainder = _E_FLAG.sub("", remainder.strip())
    text_key = normalize_product_text(remainder)
    item_keys = tuple(
        text
        for text in (
            identifier_key_text(raw, merchant_slug=merchant_slug)
            for raw in identifiers
        )
        if text is not None
    )
    full_text = normalize_product_text(line_text) or "UNKNOWN"
    if not text_key:
        # A bare identifier line still needs a stable TEXT key so the read
        # path is uniform; nutrition_key rejects blank text.
        text_key = (
            normalize_product_text(_E_FLAG.sub("", line_text.strip()))
            or full_text
        )
    return AliasKeys(
        item_keys=item_keys,
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
    if any(
        not _same_decision(left, right)
        for left, right in combinations(user_decisions, 2)
    ):
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
    # ``found`` preserves lookup order: ITEMs, TEXT, then legacy TEXT.
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

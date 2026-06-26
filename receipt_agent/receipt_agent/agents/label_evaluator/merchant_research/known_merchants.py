"""Research INPUTS for the 8 hand-validated merchants + the artifact builder.

This module encodes the per-merchant source evidence the research pipeline
triangulates — receipts (ground truth), the web/jurisdiction sales-tax rate, and
Places store identity — for the eight merchants the original hand-validation
covered, and emits ``merchant_intelligence/<slug>.json`` artifacts via the
deterministic :func:`~.research.assemble_merchant_intelligence`.

Provenance of the encoded evidence (honest about source):

* Vons, Amazon Fresh, Target — receipt evidence (effective rates, taxed counts,
  per-item taxable-rate observations, jurisdictions) read from the live receipt
  corpus via the MCP receipt-tools (dev table ``ReceiptsTable-dc5be22``). Amazon
  Fresh's per-item rate is the real Tito's-Vodka line (tax 1.16 / 15.99 =
  0.0725) isolated from a mixed basket whose effective rate is only 3.53%.
* Sprouts, Costco, The Home Depot, Gelson's, Smith's — evidence transcribed
  from the prior hand-validation session (see CONTEXT) where the corpus was read
  receipt-by-receipt; provenance strings say so.
* Web rates are documented CA/NV jurisdiction rates (CA statewide base 7.25%,
  Ventura County no district add-on; NV Clark County 8.375%; LA County ~9.5%).
  These were validated in the prior session; re-verify for NEW merchants (M5).

The emitted artifact's gate-relevant tax fields (flags, can_support, rate(s))
reproduce the hand-validated ``MERCHANT_TAX_PROFILES`` — enforced by
``test_known_merchant_artifacts.py`` — so wiring the loader changes the *source*
of the config, not its values.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from .loader import (
    matches_validated_config,
    record_approval,
    reviews_by_status,
)
from .research import (
    PlacesEvidence,
    ReceiptEvidence,
    WebEvidence,
    assemble_merchant_intelligence,
)
from .review import (
    APPROVED,
    AUTO_APPROVED,
    NEEDS_REVIEW,
    REJECTED,
    compute_review,
)
from .structure import structure_review_status, summarize_merchant_structure
from .schema import CatalogEntry, MerchantIntelligence

# Default output dir: the version-controlled artifact directory the loader reads.
ARTIFACT_DIR = Path(__file__).parent.parent / "merchant_intelligence"
# Existing per-merchant online catalogs (regenerated into the unified artifact).
_ONLINE_CATALOG_DIR = Path(__file__).parent.parent / "online_catalogs"


@dataclass(frozen=True)
class MerchantResearchInput:
    """Everything the deterministic assembler needs for one merchant."""

    merchant: str
    receipts: ReceiptEvidence
    web: WebEvidence | None = None
    places: PlacesEvidence | None = None
    block_reason: str | None = None
    catalog_file: str | None = None  # online_catalogs/<file>.json, if any
    catalog: tuple[CatalogEntry, ...] = field(default_factory=tuple)
    # Archetype distribution across this merchant's real receipts (counts per
    # archetype), measured by fingerprinting the corpus (M6). Drives the
    # structure block (M7). None when structure has not been derived yet.
    archetype_mix: dict[str, int] | None = None


def _load_catalog_file(filename: str) -> tuple[CatalogEntry, ...]:
    """Load CatalogEntry rows from an online_catalogs JSON, or () on any failure."""
    path = _ONLINE_CATALOG_DIR / filename
    if not path.exists():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    entries: list[CatalogEntry] = []
    for raw in data.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        price_raw = raw.get("price") or raw.get("line_total")
        if not name or price_raw is None:
            continue
        try:
            price = Decimal(str(price_raw))
        except Exception:
            continue
        entries.append(
            CatalogEntry(
                name=name,
                price=price,
                taxable=bool(raw.get("taxable", False)),
                source=f"online_catalog:{filename}",
            )
        )
    return tuple(entries)


# --------------------------------------------------------------------------- #
# The 8 hand-validated merchants.
# --------------------------------------------------------------------------- #

_CA_VENTURA = WebEvidence(
    jurisdiction="CA-Ventura",
    published_rate=Decimal("0.0725"),
    urls=("https://www.cdtfa.ca.gov/taxes-and-fees/sales-use-tax-rates.htm",),
    note="CA statewide base 7.25%; Ventura County no district add-on",
)

KNOWN_MERCHANTS: tuple[MerchantResearchInput, ...] = (
    MerchantResearchInput(
        merchant="Vons",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("S",),
            # Real effective rates (tax / (grand_total - tax)) from the corpus;
            # mixed baskets read low, all-taxable baskets reach ~7.3%.
            effective_rates=(
                Decimal("0.0728"),
                Decimal("0.0725"),
                Decimal("0.0730"),
                Decimal("0.0636"),
                Decimal("0.0565"),
                Decimal("0.0290"),
                Decimal("0.0062"),
            ),
            # All-taxable baskets => effective == per-item rate, snapping to 7.25%.
            observed_taxable_rates=(
                Decimal("0.0725"),
                Decimal("0.0728"),
                Decimal("0.0730"),
            ),
            receipt_count=30,
            taxed_receipt_count=10,
            blind_positive_tax_count=2,  # tax present, totals not OCR'd
        ),
        web=_CA_VENTURA,
        places=PlacesEvidence(category="grocery", jurisdictions=("CA-Ventura",)),
        # Fingerprinted 28 Vons receipts (M6): dominantly itemized retail.
        archetype_mix={
            "line_item_retail": 24,
            "service": 2,
            "restaurant_tip": 1,
            "unknown": 1,
        },
    ),
    MerchantResearchInput(
        merchant="Sprouts Farmers Market",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("F",),
            # Hand-validation: T median 7.25% across 5 clean receipts.
            effective_rates=(
                Decimal("0.0726"),
                Decimal("0.0725"),
                Decimal("0.0722"),
                Decimal("0.0724"),
            ),
            observed_taxable_rates=(Decimal("0.0725"), Decimal("0.0726")),
            receipt_count=227,
            taxed_receipt_count=5,
        ),
        web=_CA_VENTURA,
        catalog_file="sprouts_farmers_market.json",
        places=PlacesEvidence(category="grocery", jurisdictions=("CA-Ventura",)),
    ),
    MerchantResearchInput(
        merchant="Amazon Fresh",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("F",),
            # Real: tax 1.16 over a 32.85 mixed basket (effective 3.53%) whose
            # only taxable item (Tito's Vodka 15.99) gives 1.16/15.99 = 7.25%.
            effective_rates=(Decimal("0.0353"), Decimal("0.0465")),
            observed_taxable_rates=(Decimal("0.0725"),),
            receipt_count=6,
            taxed_receipt_count=2,
        ),
        web=_CA_VENTURA,
        catalog_file="amazon_fresh.json",
        places=PlacesEvidence(
            address="140 Promenade Way Ste A, Thousand Oaks, CA 91362",
            category="grocery",
            jurisdictions=("CA-Ventura",),
        ),
    ),
    MerchantResearchInput(
        merchant="Target",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("NF",),
            # Real effective rates cluster at two jurisdictions (NV ~8.37%,
            # CA ~9.5/9.7%) plus mixed-basket lows.
            effective_rates=(
                Decimal("0.0838"),
                Decimal("0.0834"),
                Decimal("0.0951"),
                Decimal("0.0972"),
                Decimal("0.0622"),
            ),
            receipt_count=20,
            taxed_receipt_count=12,
            multi_jurisdiction=True,
            per_receipt_rates_reconcile=True,
            reconciled_jurisdiction_rates=(
                Decimal("0.08375"),  # NV Clark County
                Decimal("0.0950"),  # CA LA County
                Decimal("0.0975"),  # CA (some Westlake Village receipts)
            ),
            blind_positive_tax_count=1,  # one $33-tax receipt with no totals
        ),
        web=WebEvidence(
            jurisdiction="multi",
            note="NV Clark County 8.375%, CA LA County ~9.5%; per-state",
        ),
        catalog_file="target.json",
        places=PlacesEvidence(category="department_store", jurisdictions=("NV", "CA")),
    ),
    MerchantResearchInput(
        merchant="Costco Wholesale",
        receipts=ReceiptEvidence(
            taxable_flag="A",
            nontaxable_flags=(),
            effective_rates=(Decimal("0.084"), Decimal("0.095"), Decimal("0.0975")),
            receipt_count=40,
            taxed_receipt_count=20,
            multi_jurisdiction=True,
            per_receipt_rates_reconcile=False,
        ),
        block_reason=(
            "per-item A-flag OCR too sparse; OCR drops some A-flagged items so "
            "the implied per-flag rate does not cluster"
        ),
        catalog_file="costco_wholesale.json",
        places=PlacesEvidence(category="warehouse_club", jurisdictions=("NV", "CA")),
    ),
    MerchantResearchInput(
        merchant="The Home Depot",
        receipts=ReceiptEvidence(
            taxable_flag="A",
            nontaxable_flags=(),
            effective_rates=(Decimal("0.0950"), Decimal("0.0725")),
            receipt_count=17,
            taxed_receipt_count=8,
            multi_jurisdiction=True,
            per_receipt_rates_reconcile=False,
        ),
        block_reason=(
            "only ~4 A line totals captured corpus-wide; per-item flag OCR too "
            "sparse to trust a per-item rate"
        ),
        places=PlacesEvidence(
            category="home_improvement", jurisdictions=("CA",)
        ),
    ),
    MerchantResearchInput(
        merchant="Gelson's",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("F",),
            effective_rates=(Decimal("0.0724"),),
            observed_taxable_rates=(Decimal("0.0724"),),
            receipt_count=6,
            taxed_receipt_count=1,  # only ONE physical receipt carries tax
        ),
        web=_CA_VENTURA,
        places=PlacesEvidence(category="grocery", jurisdictions=("CA-Ventura",)),
    ),
    MerchantResearchInput(
        merchant="Smith's",
        receipts=ReceiptEvidence(
            taxable_flag="",  # no T flag exists
            nontaxable_flags=("F",),
            effective_rates=(),
            receipt_count=7,
            taxed_receipt_count=0,  # NV exempts groceries; every TAX row is 0.00
        ),
        web=WebEvidence(
            jurisdiction="NV",
            note="Nevada exempts groceries; no positive tax to derive a rate",
        ),
        places=PlacesEvidence(category="grocery", jurisdictions=("NV",)),
    ),
)


# --------------------------------------------------------------------------- #
# NEW merchants the hand-validation never covered — researched end-to-end in
# this branch from the live corpus, demonstrating the pipeline extends beyond
# the original 8 with honest confidence/provenance (M5). These are NOT in
# MERCHANT_TAX_PROFILES; the artifact is the only source.
# --------------------------------------------------------------------------- #

NEW_MERCHANTS: tuple[MerchantResearchInput, ...] = (
    # CVS — a clean NEW onboarding. A real Las Vegas receipt prints
    # "PLAN B ONE STEP 49.99T" and "NV 8.375% TAX 4.19": the T flag marks the
    # taxable item and 4.19/49.99 = 0.0838 confirms NV Clark County 8.375%.
    MerchantResearchInput(
        merchant="CVS",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=(),
            effective_rates=(
                Decimal("0.0841"),
                Decimal("0.0838"),
                Decimal("0.0838"),
                Decimal("0.0433"),
            ),
            # Near-all-taxable baskets => effective == per-item rate, snapping to
            # the NV 8.375% the receipts literally print.
            observed_taxable_rates=(Decimal("0.0838"), Decimal("0.0841")),
            receipt_count=18,
            taxed_receipt_count=4,
        ),
        web=WebEvidence(
            jurisdiction="NV-Clark",
            published_rate=Decimal("0.08375"),
            urls=("https://tax.nv.gov/",),
            note="NV Clark County 8.375%; rate printed on the receipt itself",
        ),
        places=PlacesEvidence(
            address="3810 E Sunset Rd, Las Vegas, NV 89120",
            category="pharmacy",
            jurisdictions=("NV-Clark",),
        ),
    ),
    # Trader Joe's — researched, correctly REFUSED. The observed TAX/SUBTOTAL
    # labels are mislabeled payment splits ("Local Cash $4.00", "VISA $32.71"),
    # and the sampled stores are Henderson NV where groceries are exempt. No
    # reliable taxable-item rate exists, so support stays OFF.
    MerchantResearchInput(
        merchant="Trader Joe's",
        receipts=ReceiptEvidence(
            taxable_flag="",  # no reliable tax-class flag observed
            nontaxable_flags=(),
            effective_rates=(),  # the "tax" labels are payment splits, not tax
            receipt_count=19,
            taxed_receipt_count=4,  # mislabeled; not real tax observations
        ),
        block_reason=(
            "observed TAX/SUBTOTAL labels are mislabeled payment splits "
            "(e.g. 'Local Cash $4.00', 'VISA $32.71'); sampled stores are "
            "Henderson NV where groceries are exempt; no reliable taxable-item "
            "rate"
        ),
        places=PlacesEvidence(category="grocery", jurisdictions=("NV",)),
    ),
    # Whole Foods Market — researched, OFF for insufficient evidence: only one
    # taxed receipt in the corpus (single-receipt arithmetic, not a
    # cross-receipt confirmation), and the taxable flag was not confirmed.
    MerchantResearchInput(
        merchant="Whole Foods Market",
        receipts=ReceiptEvidence(
            taxable_flag="",  # not confirmed from receipts yet
            nontaxable_flags=(),
            effective_rates=(Decimal("0.0838"),),
            receipt_count=5,
            taxed_receipt_count=1,  # only ONE taxed receipt
        ),
        places=PlacesEvidence(category="grocery", jurisdictions=("",)),
    ),
    # Tan L.A. — a SERVICE merchant (M7): a tanning salon whose receipts are a
    # single service charge + total, NO line-item grid. Fingerprinting its
    # receipts (M6) puts the majority in the `service` archetype, so synthesis
    # uses field/amount/header ops (no line-item ops) and a no-line-item receipt
    # is valid grounding rather than "missing line items". No reliable sales-tax
    # evidence (a service with no itemized taxable goods), so tax stays off.
    MerchantResearchInput(
        merchant="Tan L.A.",
        receipts=ReceiptEvidence(
            taxable_flag="",
            nontaxable_flags=(),
            effective_rates=(),
            receipt_count=3,
            taxed_receipt_count=0,
        ),
        places=PlacesEvidence(category="service", jurisdictions=("CA-Ventura",)),
        archetype_mix={"service": 2, "line_item_retail": 1},
    ),
)

# Every committed artifact must be reproducible by this builder.
ALL_MERCHANTS: tuple[MerchantResearchInput, ...] = KNOWN_MERCHANTS + NEW_MERCHANTS


def build_known_artifacts(generated_at: str) -> dict[str, MerchantIntelligence]:
    """Assemble MerchantIntelligence for every known + new merchant, by slug."""
    out: dict[str, MerchantIntelligence] = {}
    for spec in ALL_MERCHANTS:
        catalog: tuple[CatalogEntry, ...] = spec.catalog
        if spec.catalog_file:
            catalog = catalog + _load_catalog_file(spec.catalog_file)
        structure: dict | None = None
        if spec.archetype_mix:
            ms = summarize_merchant_structure(spec.archetype_mix)
            structure = ms.to_dict()
            # Deterministic gate status (recomputed by the loader; high -> auto).
            structure["status"] = structure_review_status(ms.confidence)
        intel = assemble_merchant_intelligence(
            spec.merchant,
            receipts=spec.receipts,
            web=spec.web,
            places=spec.places,
            catalog=catalog,
            generated_at=generated_at,
            block_reason=spec.block_reason,
            structure=structure,
        )
        out[intel.slug] = intel
    return out


def build_artifact_payloads(generated_at: str) -> dict[str, dict]:
    """Full JSON payload (intel + deterministic review block) per slug.

    The review block is written into the artifact for visibility, but the gate
    always RECOMPUTES it in the loader — the stored block is never trusted, so a
    hand-edited "auto_approved" cannot enable anything.
    """
    payloads: dict[str, dict] = {}
    for slug, intel in build_known_artifacts(generated_at).items():
        payload = intel.to_dict()
        payload["review"] = compute_review(
            intel, matches_validated_config=matches_validated_config(intel)
        ).to_dict()
        payloads[slug] = payload
    return payloads


def write_artifacts(
    out_dir: Path, generated_at: str, *, slugs: Sequence[str] | None = None
) -> list[Path]:
    """Write known + new merchant artifacts to ``out_dir`` as ``<slug>.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for slug, payload in build_artifact_payloads(generated_at).items():
        if slugs is not None and slug not in slugs:
            continue
        path = out_dir / f"{slug}.json"
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def _cmd_write(args: argparse.Namespace) -> None:
    for path in write_artifacts(args.out_dir, args.generated_at):
        print(path)


def _cmd_list(args: argparse.Namespace) -> None:
    grouped = reviews_by_status()
    order = [NEEDS_REVIEW, REJECTED, AUTO_APPROVED, APPROVED]
    statuses = [NEEDS_REVIEW] if args.needs_review else order
    for status in statuses:
        items = sorted(grouped.get(status, []))
        if not items:
            continue
        print(f"\n{status} ({len(items)}):")
        for slug, review in items:
            enabling = "ENABLES edits" if review.is_enabling() else "parked"
            print(f"  {slug:26s} [{enabling}]")
            for reason in review.reasons:
                print(f"      - {reason}")


def _cmd_approve(args: argparse.Namespace) -> None:
    entry = record_approval(
        args.slug,
        approved_by=args.by,
        approved_at=args.at or _now_iso(),
        note=args.note or "",
        kind=args.kind,
    )
    print(
        f"recorded {entry['kind']} approval for {entry['slug']} "
        f"(hash={entry['content_hash'][:12]}…) by {entry['approved_by']}"
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_write = sub.add_parser("write", help="(re)generate the artifact JSON files")
    p_write.add_argument("--out-dir", type=Path, default=ARTIFACT_DIR)
    p_write.add_argument(
        "--generated-at",
        required=True,
        help="ISO timestamp stamped into each artifact (kept out of code so the "
        "build stays deterministic/replayable).",
    )
    p_write.set_defaults(func=_cmd_write)

    p_list = sub.add_parser("list", help="show each artifact's review status")
    p_list.add_argument(
        "--needs-review",
        action="store_true",
        help="show only artifacts parked awaiting human review",
    )
    p_list.set_defaults(func=_cmd_list)

    p_appr = sub.add_parser(
        "approve", help="record a human sign-off for a merchant's current tax block"
    )
    p_appr.add_argument("slug", help="merchant slug to approve")
    p_appr.add_argument("--by", required=True, help="approver identity")
    p_appr.add_argument(
        "--kind", choices=("tax", "structure"), default="tax",
        help="which block to sign off (default: tax)",
    )
    p_appr.add_argument("--at", default="", help="ISO timestamp (default: now, UTC)")
    p_appr.add_argument("--note", default="", help="optional review note")
    p_appr.set_defaults(func=_cmd_approve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    _main()

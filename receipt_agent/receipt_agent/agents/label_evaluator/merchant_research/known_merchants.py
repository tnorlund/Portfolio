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

from .research import (
    PlacesEvidence,
    ReceiptEvidence,
    WebEvidence,
    assemble_merchant_intelligence,
)
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


def build_known_artifacts(generated_at: str) -> dict[str, MerchantIntelligence]:
    """Assemble MerchantIntelligence for every known merchant, keyed by slug."""
    out: dict[str, MerchantIntelligence] = {}
    for spec in KNOWN_MERCHANTS:
        catalog: tuple[CatalogEntry, ...] = spec.catalog
        if spec.catalog_file:
            catalog = catalog + _load_catalog_file(spec.catalog_file)
        intel = assemble_merchant_intelligence(
            spec.merchant,
            receipts=spec.receipts,
            web=spec.web,
            places=spec.places,
            catalog=catalog,
            generated_at=generated_at,
            block_reason=spec.block_reason,
        )
        out[intel.slug] = intel
    return out


def write_artifacts(
    out_dir: Path, generated_at: str, *, slugs: Sequence[str] | None = None
) -> list[Path]:
    """Write known-merchant artifacts to ``out_dir`` as ``<slug>.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for slug, intel in build_known_artifacts(generated_at).items():
        if slugs is not None and slug not in slugs:
            continue
        path = out_dir / f"{slug}.json"
        path.write_text(
            json.dumps(intel.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ARTIFACT_DIR,
        help="Directory to write <slug>.json artifacts (default: merchant_intelligence/).",
    )
    parser.add_argument(
        "--generated-at",
        required=True,
        help="ISO timestamp to stamp into each artifact (kept out of code so the "
        "build stays deterministic/replayable).",
    )
    args = parser.parse_args()
    written = write_artifacts(args.out_dir, args.generated_at)
    for path in written:
        print(path)


if __name__ == "__main__":
    _main()

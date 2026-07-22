"""Contract tests for scripts/ingest_merchant_catalog.py (W-E).

Stub-client tests (pattern: tests/test_activate_merchant_truth.py) prove:

* the y-band miner matches a priced word only to same-row name words on its
  LEFT (multi-column receipt fixture), with section-header categories;
* the NON_PRODUCT stoplist and the negative-total guard keep
  tax/totals/coupon rows out of the catalog;
* the attribution spot-check QUARANTINES a conflicted receipt (the known
  BJ's case: costco-attributed, BJ's/Henderson content) — excluded from
  mining and reported, never silently mined;
* dry-run performs zero writes (poisoned stub goes red if the ``--apply``
  gate is removed);
* the prod table is refused unconditionally before any client construction
  (poisoned DynamoClient constructor);
* every mined item records non-empty source_receipt_keys provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from receipt_dynamo.entities.merchant_catalog_item import MerchantCatalogItem
from scripts import ingest_merchant_catalog as imc

# ---------------------------------------------------------------------------
# Fixture receipts (fake OCR words / labels / places)
# ---------------------------------------------------------------------------


@dataclass
class FakeWord:
    line_id: int
    word_id: int
    text: str
    x: float
    y: float
    h: float = 0.01

    @property
    def top_left(self) -> dict[str, float]:
        return {"x": self.x - 0.02, "y": self.y - self.h / 2}

    @property
    def bottom_right(self) -> dict[str, float]:
        return {"x": self.x + 0.02, "y": self.y + self.h / 2}


@dataclass
class FakeLabel:
    line_id: int
    word_id: int
    label: str
    reasoning: str | None = None


@dataclass
class FakeDetails:
    words: list[FakeWord]
    labels: list[FakeLabel]


@dataclass
class FakePlace:
    image_id: str
    receipt_id: int
    merchant_name: str


def _receipt(
    rows: list[tuple[str, str, float, str | None]],
    header: list[str],
    sections: list[tuple[str, float]] = (),
    reasonings: dict[int, str] | None = None,
) -> FakeDetails:
    """Build a fake receipt.

    rows: (name_text, price_text, y, price_label_or_None); the name words
    go at x ~0.1.., the price at x=0.80. header: unlabeled words at the top.
    sections: (keyword, y) unlabeled section-header words.
    reasonings: row index -> reasoning text attached to that row's label.
    """
    words: list[FakeWord] = []
    labels: list[FakeLabel] = []
    line_id = 0
    for i, text in enumerate(header):
        words.append(FakeWord(line_id, i, text, 0.1 + 0.12 * i, 0.05))
    for keyword, y in sections:
        line_id += 1
        words.append(FakeWord(line_id, 0, keyword, 0.1, y))
    for index, (name_text, price_text, y, price_label) in enumerate(rows):
        line_id += 1
        for word_index, token in enumerate(name_text.split()):
            words.append(
                FakeWord(
                    line_id, word_index, token, 0.08 + 0.1 * word_index, y
                )
            )
            labels.append(
                FakeLabel(
                    line_id,
                    word_index,
                    "PRODUCT_NAME",
                    (reasonings or {}).get(index),
                )
            )
        line_id += 1
        words.append(FakeWord(line_id, 0, price_text, 0.80, y))
        if price_label:
            labels.append(FakeLabel(line_id, 0, price_label))
    return FakeDetails(words=words, labels=labels)


def costco_receipt_a() -> FakeDetails:
    """Multi-column Costco receipt: two product rows under two headers."""
    return _receipt(
        rows=[
            ("KS WATER", "3.99", 0.30, "LINE_TOTAL"),
            ("ORG SPINACH", "4.49", 0.40, "ITEM_TOTAL"),
            # stoplist rows: priced but not products
            ("SUBTOTAL", "8.48", 0.60, "LINE_TOTAL"),
            ("TAX", "0.70", 0.64, "LINE_TOTAL"),
            # markdown/credit: negative total is never a product
            ("KS WATER", "-1.00", 0.68, "LINE_TOTAL"),
        ],
        header=["COSTCO", "WHOLESALE"],
        sections=[("GROCERY", 0.28), ("PRODUCE", 0.39)],
    )


def costco_receipt_b() -> FakeDetails:
    """Second clean receipt observing KS WATER again (provenance union)."""
    return _receipt(
        rows=[("KS WATER", "3.99", 0.30, "LINE_TOTAL")],
        header=["COSTCO", "WHOLESALE"],
    )


def bjs_conflict_receipt() -> FakeDetails:
    """The known case: costco-attributed receipt with BJ's content."""
    return _receipt(
        rows=[("PAPER TOWELS", "12.99", 0.30, "LINE_TOTAL")],
        header=["BJ'S", "WHOLESALE", "CLUB", "HENDERSON", "NV"],
        reasonings={0: "Product line on the BJ's Henderson receipt"},
    )


# ---------------------------------------------------------------------------
# Stub client (reads served from fixtures; writes poisoned unless allowed)
# ---------------------------------------------------------------------------


class StubClient:
    def __init__(
        self,
        receipts: dict[tuple[str, int], FakeDetails],
        merchant_name: str = "Costco Wholesale",
        *,
        allow_writes: bool = False,
    ) -> None:
        self.receipts = receipts
        self.merchant_name = merchant_name
        self.allow_writes = allow_writes
        self.write_calls: list[tuple[str, Any]] = []
        self.written_items: list[MerchantCatalogItem] = []

    # -- reads ------------------------------------------------------------
    def get_receipt_places_by_merchant(self, merchant_name: str):
        places = [
            FakePlace(image_id, receipt_id, self.merchant_name)
            for image_id, receipt_id in self.receipts
        ]
        return places, None

    def get_receipt_details(self, image_id: str, receipt_id: int):
        return self.receipts[(image_id, receipt_id)]

    # -- writes -----------------------------------------------------------
    def delete_merchant_catalog(self, merchant_name: str) -> None:
        if not self.allow_writes:
            raise AssertionError(
                "dry-run must never call delete_merchant_catalog"
            )
        self.write_calls.append(("delete", merchant_name))

    def add_merchant_catalog_items(
        self, items: list[MerchantCatalogItem]
    ) -> None:
        if not self.allow_writes:
            raise AssertionError(
                "dry-run must never call add_merchant_catalog_items"
            )
        self.write_calls.append(("add", len(items)))
        self.written_items.extend(items)

    def put_merchant_catalog_items(
        self, items: list[MerchantCatalogItem]
    ) -> None:
        if not self.allow_writes:
            raise AssertionError(
                "dry-run must never call put_merchant_catalog_items"
            )
        self.write_calls.append(("put", len(items)))


# ---------------------------------------------------------------------------
# Miner: y-band matching on the multi-column fixture
# ---------------------------------------------------------------------------


def test_yband_matching_multi_column() -> None:
    rows = imc.mine_receipt("img-a#00001", costco_receipt_a())
    by_name = {row["product_text"]: row for row in rows}
    assert set(by_name) == {"KS WATER", "ORG SPINACH"}
    # section-header categories: nearest header row by y-distance
    assert by_name["KS WATER"]["category"] == "GROCERY"
    assert by_name["ORG SPINACH"]["category"] == "PRODUCE"
    assert by_name["KS WATER"]["price"] == "3.99"
    assert by_name["KS WATER"]["receipt_key"] == "img-a#00001"


def test_price_never_matches_names_to_its_right() -> None:
    details = _receipt(
        rows=[("KS WATER", "3.99", 0.30, "LINE_TOTAL")],
        header=["COSTCO"],
    )
    # Move every name word to the RIGHT of the price column.
    for word in details.words:
        if word.text in {"KS", "WATER"}:
            word.x = 0.90
    assert imc.mine_receipt("img#00001", details) == []


def test_price_never_matches_names_in_other_y_bands() -> None:
    details = _receipt(
        rows=[
            ("KS WATER", "3.99", 0.30, "LINE_TOTAL"),
            ("ORG SPINACH", "4.49", 0.40, "ITEM_TOTAL"),
        ],
        header=["COSTCO"],
    )
    rows = imc.mine_receipt("img#00001", details)
    prices = {row["product_text"]: row["price"] for row in rows}
    # Each price matched its own row's name, not the neighbor 0.10 away.
    assert prices == {"KS WATER": "3.99", "ORG SPINACH": "4.49"}


def test_stoplist_and_negative_totals_excluded() -> None:
    rows = imc.mine_receipt("img-a#00001", costco_receipt_a())
    names = {row["product_text"] for row in rows}
    assert "SUBTOTAL" not in names
    assert "TAX" not in names
    # the -1.00 markdown produced no extra KS WATER observation
    assert sum(1 for row in rows if row["product_text"] == "KS WATER") == 1


def test_every_mined_item_records_provenance() -> None:
    mined = [
        *imc.mine_receipt("img-a#00001", costco_receipt_a()),
        *imc.mine_receipt("img-b#00001", costco_receipt_b()),
    ]
    items = imc.build_catalog(
        "Costco Wholesale", imc.aggregate_observations(mined)
    )
    assert items, "fixture must mine at least one item"
    for item in items:
        assert item.source == "observed"
        assert item.source_receipt_keys, item.product_text
    water = next(i for i in items if i.product_text == "KS WATER")
    assert water.observed_count == 2
    assert water.source_receipt_keys == ["img-a#00001", "img-b#00001"]


# ---------------------------------------------------------------------------
# Attribution spot-check: the BJ's quarantine
# ---------------------------------------------------------------------------


def test_conflicted_receipt_quarantined_and_reported() -> None:
    check = imc.check_attribution(
        "Costco Wholesale", "img-c#00002", bjs_conflict_receipt()
    )
    assert check.verdict == "QUARANTINED"
    assert check.attributed_brand == "costco"
    assert any("bjs_wholesale" in hit for hit in check.conflicting)
    # both OCR and label-reasoning signals are captured as evidence
    assert any(
        hit.startswith("[bjs_wholesale] ocr:") for hit in check.conflicting
    )
    assert any(
        hit.startswith("[bjs_wholesale] reasoning:")
        for hit in check.conflicting
    )
    assert not check.supporting


def test_target_prose_in_reasoning_does_not_quarantine() -> None:
    """Regression from the real dev run: LLM label reasoning says "the
    TARGET line" constantly — generic-word brands are OCR-only signals."""
    details = _receipt(
        rows=[("KS WATER", "3.99", 0.30, "LINE_TOTAL")],
        header=["COSTCO", "WHOLESALE"],
        reasonings={0: "The amount appears in the TARGET line of the receipt"},
    )
    check = imc.check_attribution("Costco Wholesale", "img#00001", details)
    assert check.verdict == "OK"
    assert not check.conflicting


def test_target_in_ocr_still_conflicts() -> None:
    """A literal TARGET header in the OCR text is real foreign evidence."""
    details = _receipt(
        rows=[("KS WATER", "3.99", 0.30, "LINE_TOTAL")],
        header=["TARGET"],
    )
    check = imc.check_attribution("Costco Wholesale", "img#00001", details)
    assert check.verdict == "QUARANTINED"
    assert any(hit.startswith("[target] ocr:") for hit in check.conflicting)


def test_clean_receipt_passes_attribution() -> None:
    check = imc.check_attribution(
        "Costco Wholesale", "img-a#00001", costco_receipt_a()
    )
    assert check.verdict == "OK"
    assert check.supporting


def test_quarantined_receipt_excluded_from_mining(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StubClient(
        {
            ("img-a", 1): costco_receipt_a(),
            ("img-c", 2): bjs_conflict_receipt(),
        },
        allow_writes=False,
    )
    report_path = tmp_path / "attribution.json"
    dump_path = tmp_path / "items.json"

    exit_code = imc.main(
        [
            "--merchant",
            "Costco Wholesale",
            "--attribution-report",
            str(report_path),
            "--dump",
            str(dump_path),
        ],
        client=client,
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text())
    assert report["receipts_checked"] == 2
    assert report["verdicts"]["QUARANTINED"] == 1
    [quarantined] = report["quarantined"]
    assert quarantined["receipt_key"] == "img-c#00002"
    assert quarantined["verdict"] == "QUARANTINED"
    assert quarantined["conflicting"]

    # the conflicted receipt's rows never reach the catalog
    dumped = json.loads(dump_path.read_text())
    names = {entry["product_text"] for entry in dumped}
    assert "PAPER TOWELS" not in names
    assert "KS WATER" in names
    for entry in dumped:
        assert entry["source_receipt_keys"]
        assert "img-c#00002" not in entry["source_receipt_keys"]

    output = capsys.readouterr().out
    assert "QUARANTINED img-c#00002" in output


# ---------------------------------------------------------------------------
# Dry-run: zero writes (mutation-honesty guard for the --apply gate)
# ---------------------------------------------------------------------------


def test_dry_run_performs_zero_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = StubClient({("img-a", 1): costco_receipt_a()}, allow_writes=False)
    exit_code = imc.main(["--merchant", "Costco Wholesale"], client=client)
    assert exit_code == 0
    assert client.write_calls == []
    output = capsys.readouterr().out
    assert "DRY-RUN: no writes performed" in output
    assert "--apply" in output


def test_apply_rewrites_partition_with_provenance() -> None:
    client = StubClient(
        {
            ("img-a", 1): costco_receipt_a(),
            ("img-c", 2): bjs_conflict_receipt(),
        },
        allow_writes=True,
    )
    exit_code = imc.main(
        ["--merchant", "Costco Wholesale", "--apply"], client=client
    )
    assert exit_code == 0
    assert [call[0] for call in client.write_calls] == ["delete", "add"]
    assert client.written_items
    for item in client.written_items:
        assert item.source == "observed"
        assert item.source_receipt_keys
        assert "img-c#00002" not in item.source_receipt_keys


def test_apply_refuses_to_clear_when_nothing_mined() -> None:
    client = StubClient({}, allow_writes=True)
    exit_code = imc.main(
        ["--merchant", "Costco Wholesale", "--apply"], client=client
    )
    assert exit_code == 1
    assert client.write_calls == []


# ---------------------------------------------------------------------------
# Prod refusal: unconditional, before any client construction
# ---------------------------------------------------------------------------


def test_prod_table_refused_before_any_read_or_write(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import receipt_dynamo.data.dynamo_client as dynamo_client_module

    def poisoned(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "prod guard must refuse before any client construction"
        )

    monkeypatch.setattr(dynamo_client_module, "DynamoClient", poisoned)
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", imc.PROD_TABLE_NAME)

    exit_code = imc.main(
        ["--merchant", "Costco Wholesale", "--apply"], client=None
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "table",
    ["ReceiptsTable-d7ff76a", "ReceiptsTable-d7ff76a-copy", "x-d7ff76a-x"],
)
def test_resolve_table_refuses_prod_names_and_marker(table: str) -> None:
    with pytest.raises(ValueError, match="never touches prod"):
        imc.resolve_table(table)


def test_resolve_table_defaults_to_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    assert imc.resolve_table(None) == imc.DEV_TABLE_NAME

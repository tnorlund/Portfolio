"""Tests for the line-item reconciliation tools on both MCP servers.

The two servers (stdio ``scripts/receipt_mcp_server.py`` and the Lambda
``infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py``) must
expose the same tool surface. These tests import each module with a
minimal fake ``mcp`` package (the real dependency is not installed in
CI), assert the three line-item tools are registered with valid input
schemas and matching impls, and exercise the impl functions with stub
dynamo clients — in particular the arithmetic guard of
``extend_items_section``, which must refuse any extension that does not
strictly shrink |delta| AND improve the reconciliation status.
"""

import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVER_FILES = {
    "stdio": REPO_ROOT / "scripts" / "receipt_mcp_server.py",
    "lambda": (
        REPO_ROOT
        / "infra"
        / "mcp_server_lambda"
        / "lambdas"
        / "receipt_mcp_server_server.py"
    ),
}

EXPECTED_LINE_ITEM_TOOLS = {
    "get_receipt_line_items",
    "extend_items_section",
    "list_reconciliation_worklist",
}

VALID_IMAGE_ID = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"
OTHER_IMAGE_ID = "9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4"


class _FakeTool:
    """Stand-in for mcp.types.Tool that just records its fields."""

    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


class _FakeContent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _install_mcp_stubs():
    """Register a minimal fake `mcp` package so the servers import cleanly."""
    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    stdio_mod = types.ModuleType("mcp.server.stdio")
    types_mod = types.ModuleType("mcp.types")

    class _FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            def decorator(func):
                return func

            return decorator

        def call_tool(self):
            def decorator(func):
                return func

            return decorator

    def _fake_stdio_server(*args, **kwargs):  # pragma: no cover - unused
        raise RuntimeError("stdio_server is not exercised in tests")

    server_mod.Server = _FakeServer
    stdio_mod.stdio_server = _fake_stdio_server
    types_mod.Tool = _FakeTool
    types_mod.TextContent = _FakeContent
    types_mod.ImageContent = _FakeContent

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.stdio"] = stdio_mod
    sys.modules["mcp.types"] = types_mod


def _load_module(label, path):
    _install_mcp_stubs()
    spec = importlib.util.spec_from_file_location(
        f"receipt_mcp_server_{label}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Registration / schema parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_line_item_tools_present_with_valid_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = asyncio.run(module.list_tools())
    by_name = {t.name: t for t in tools}

    missing = EXPECTED_LINE_ITEM_TOOLS - set(by_name)
    assert not missing, f"missing line-item tools in {label}: {missing}"

    for name in EXPECTED_LINE_ITEM_TOOLS:
        tool = by_name[name]
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        assert isinstance(schema.get("properties"), dict)
        assert isinstance(tool.description, str) and tool.description.strip()

    assert set(by_name["get_receipt_line_items"].inputSchema["required"]) == {
        "image_id",
        "receipt_id",
    }

    extend_schema = by_name["extend_items_section"].inputSchema
    assert set(extend_schema["required"]) == {
        "image_id",
        "receipt_id",
        "add_line_ids",
    }
    # dry_run must default to True: the write path is opt-in.
    assert extend_schema["properties"]["dry_run"]["default"] is True
    assert extend_schema["properties"]["add_line_ids"]["items"] == {
        "type": "integer"
    }

    worklist_schema = by_name["list_reconciliation_worklist"].inputSchema
    assert "required" not in worklist_schema
    assert worklist_schema["properties"]["status"]["default"] == "mismatch"
    assert set(worklist_schema["properties"]["status"]["enum"]) == {
        "mismatch",
        "near",
        "match",
        "no-baseline",
    }


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_line_item_tool_impls_exist(label):
    module = _load_module(label, SERVER_FILES[label])
    for name in EXPECTED_LINE_ITEM_TOOLS:
        impl = getattr(module, f"{name}_impl", None)
        assert callable(impl), f"missing {name}_impl in {label} server"


def test_both_servers_expose_identical_line_item_tool_shape():
    stdio = _load_module("stdio-li", SERVER_FILES["stdio"])
    lam = _load_module("lambda-li", SERVER_FILES["lambda"])

    def shape(module):
        tools = asyncio.run(module.list_tools())
        return {
            t.name: (t.description, t.inputSchema)
            for t in tools
            if t.name in EXPECTED_LINE_ITEM_TOOLS
        }

    assert shape(stdio) == shape(lam)


# ---------------------------------------------------------------------------
# Stub receipt world: a 4-line receipt whose arithmetic is fully known.
#   line 1  APPLES   3.00   (in ITEMS)
#   line 2  BANANAS  2.00   (in ITEMS)
#   line 3  ORANGES  4.00   (outside ITEMS — the zone gap)
#   line 4  JUNKTHING 50.00 (outside ITEMS — absorbing it breaks arithmetic)
# summary: subtotal 9.00, tax 0.72, grand_total 9.72
# Current zone {1,2} reconciles mismatch (5 vs 9); {1,2,3} is a match;
# {1,2,3,4} is a worse mismatch (59 vs 9).
# ---------------------------------------------------------------------------


def _word(line_id, word_id, text, x, y):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y - 0.01, "width": 0.1, "height": 0.02},
    )


def _receipt_words():
    return [
        _word(1, 1, "APPLES", 0.05, 0.10),
        _word(1, 2, "3.00", 0.80, 0.10),
        _word(2, 1, "BANANAS", 0.05, 0.15),
        _word(2, 2, "2.00", 0.80, 0.15),
        _word(3, 1, "ORANGES", 0.05, 0.20),
        _word(3, 2, "4.00", 0.80, 0.20),
        _word(4, 1, "JUNKTHING", 0.05, 0.25),
        _word(4, 2, "50.00", 0.80, 0.25),
    ]


def _summary_record(subtotal=9.00, tax=0.72, grand_total=9.72):
    """A real ReceiptSummaryRecord (the apply path rebuilds one from it)."""
    from receipt_dynamo.entities.receipt_summary import (
        MonetaryTotals,
        ReceiptSummary,
    )
    from receipt_dynamo.entities.receipt_summary_record import (
        ReceiptSummaryRecord,
    )

    summary = ReceiptSummary(
        image_id=VALID_IMAGE_ID,
        receipt_id=1,
        merchant_name="Test Mart",
        totals=MonetaryTotals(
            grand_total=grand_total, subtotal=subtotal, tax=tax
        ),
        item_count=3,
    )
    return ReceiptSummaryRecord(
        summary=summary,
        timestamp_computed="2026-01-01T00:00:00+00:00",
    )


def _items_section(line_ids=(1, 2), validation_status="VALID"):
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    return ReceiptSection(
        receipt_id=1,
        image_id=VALID_IMAGE_ID,
        section_type="ITEMS",
        line_ids=list(line_ids),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model_source="section-seed-v0",
        validation_status=validation_status,
    )


class _StubDynamoClient:
    """Stub dynamo client for the extend/diagnostic impls."""

    def __init__(
        self,
        sections=None,
        summary_record="default",
        line_items=(),
    ):
        self.sections = sections if sections is not None else []
        self.summary_record = (
            _summary_record()
            if summary_record == "default"
            else summary_record
        )
        self.line_items = list(line_items)
        self.updated_sections = []
        self.updated_summaries = []

    def get_receipt_details(self, image_id, receipt_id):
        return SimpleNamespace(
            lines=[SimpleNamespace(line_id=i) for i in (1, 2, 3, 4)],
            words=_receipt_words(),
        )

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return self.sections

    def get_receipt_summary(self, image_id, receipt_id):
        if self.summary_record is None:
            from receipt_dynamo.data.shared_exceptions import (
                EntityNotFoundError,
            )

            raise EntityNotFoundError("summary not found")
        return self.summary_record

    def get_receipt_line_items_from_receipt(self, image_id, receipt_id):
        return self.line_items

    def update_receipt_section(self, section):
        self.updated_sections.append(section)

    def update_receipt_summary(self, record):
        self.updated_summaries.append(record)


# ---------------------------------------------------------------------------
# get_receipt_line_items_impl
# ---------------------------------------------------------------------------


def _line_item(
    item_index,
    name,
    price,
    is_discount=False,
    reconciliation_status="mismatch",
    merchant_name="Test Mart",
    image_id=VALID_IMAGE_ID,
    receipt_id=1,
):
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        item_index=item_index,
        name=name,
        price=str(price),
        quantity=None,
        unit_price=None,
        is_discount=is_discount,
        line_ids=[item_index + 1],
        name_quality="ok",
        merchant_name=merchant_name,
        reconciliation_status=reconciliation_status,
        extractor_version="line-items-blocks-v2",
    )


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_get_receipt_line_items_diagnostic_view(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(
        sections=[_items_section(line_ids=(1, 2))],
        line_items=[
            _line_item(0, "APPLES", "3.00"),
            _line_item(1, "BANANAS", "2.00"),
            _line_item(2, "COUPON", "-1.00", is_discount=True),
        ],
    )
    result = asyncio.run(
        module.get_receipt_line_items_impl(client, VALID_IMAGE_ID, 1)
    )

    assert "error" not in result
    assert result["item_count"] == 3
    # Leftover coupon is not in the printed subtotal, so the helper
    # keeps the discount-excluded sum.
    assert result["items_sum"] == 5.00
    assert result["summary"]["subtotal"] == 9.00
    assert result["summary"]["merchant_name"] == "Test Mart"
    assert result["delta"] == -4.00
    assert result["reconciliation_status"] == "mismatch"
    assert result["items_section_line_ids"] == [1, 2]
    assert result["items_section_status"] == "VALID"
    first = result["items"][0]
    assert first["name"] == "APPLES"
    assert first["price"] == 3.00
    assert first["extractor_version"] == "line-items-blocks-v2"
    assert first["name_quality"] == "ok"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_get_receipt_line_items_counts_bogo_discount(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(
        sections=[_items_section(line_ids=(1, 2, 3))],
        summary_record=_summary_record(
            subtotal=5.98, tax=0.48, grand_total=6.46
        ),
        line_items=[
            _line_item(0, "PENNE", "3.99"),
            _line_item(1, "PENNE", "3.99"),
            _line_item(2, "BOGO", "-2.00", is_discount=True),
        ],
    )
    result = asyncio.run(
        module.get_receipt_line_items_impl(client, VALID_IMAGE_ID, 1)
    )
    assert "error" not in result
    assert result["items_sum"] == 5.98
    assert result["delta"] == 0.00


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_get_receipt_line_items_without_summary(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(
        sections=[],
        summary_record=None,
        line_items=[_line_item(0, "APPLES", "3.00")],
    )
    result = asyncio.run(
        module.get_receipt_line_items_impl(client, VALID_IMAGE_ID, 1)
    )
    assert "error" not in result
    assert result["summary"] is None
    assert result["delta"] is None
    assert result["items_section_line_ids"] is None


# ---------------------------------------------------------------------------
# extend_items_section_impl — the arithmetic guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_verified_dry_run_does_not_write(label):
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(sections=[_items_section(line_ids=(1, 2))])

    result = asyncio.run(
        module.extend_items_section_impl(client, VALID_IMAGE_ID, 1, [3])
    )

    assert "error" not in result
    assert result["verified"] is True
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert result["before"]["status"] == "mismatch"
    assert result["after"]["status"] == "match"
    assert abs(result["after"]["delta"]) < abs(result["before"]["delta"])
    assert client.updated_sections == []
    assert client.updated_summaries == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_apply_updates_section_and_rewrites_summary(label):
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(sections=[_items_section(line_ids=(1, 2))])

    result = asyncio.run(
        module.extend_items_section_impl(
            client, VALID_IMAGE_ID, 1, [3], dry_run=False
        )
    )

    assert "error" not in result
    assert result["verified"] is True
    assert result["applied"] is True
    assert result["summary_rewritten"] is True

    assert len(client.updated_sections) == 1
    written = client.updated_sections[0]
    assert written.section_type == "ITEMS"
    assert sorted(written.line_ids) == [1, 2, 3]
    # The repair_item_sections lesson: never demote a VALID section.
    assert written.validation_status == "VALID"
    assert "mcp-extend-items-v1" in written.model_source

    # Summary rewritten with a FRESH timestamp so the stream stage
    # regenerates the RECEIPT_LINE_ITEM rows.
    assert len(client.updated_summaries) == 1
    rewritten = client.updated_summaries[0]
    assert rewritten.timestamp_computed != "2026-01-01T00:00:00+00:00"
    assert rewritten.subtotal == 9.00
    assert rewritten.merchant_name == "Test Mart"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_refuses_when_arithmetic_worsens(label):
    """Absorbing a junk 50.00 band breaks reconciliation — must refuse.

    The absorbed sum (59.00) overwhelms the printed baseline (13.00),
    tripping reconcile's baseline sanity check (item_sum > 3x baseline
    -> no-baseline), so the guard refuses for lack of comparable
    deltas rather than via the shrink-and-improve check.
    """
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(sections=[_items_section(line_ids=(1, 2))])

    result = asyncio.run(
        module.extend_items_section_impl(
            client, VALID_IMAGE_ID, 1, [4], dry_run=False
        )
    )

    assert "error" not in result
    assert result["verified"] is False
    assert result["applied"] is False
    assert "refusal" in result
    assert result["before"]["delta"] == -4.00
    assert result["after"]["status"] == "no-baseline"
    assert result["after"]["delta"] is None
    assert client.updated_sections == []
    assert client.updated_summaries == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_refuses_when_already_matching(label):
    pytest.importorskip("receipt_dynamo")
    pytest.importorskip("receipt_upload")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(sections=[_items_section(line_ids=(1, 2, 3))])

    result = asyncio.run(
        module.extend_items_section_impl(
            client, VALID_IMAGE_ID, 1, [4], dry_run=False
        )
    )

    assert result["verified"] is False
    assert "refusal" in result
    assert client.updated_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_refuses_lines_claimed_by_other_sections(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    summary_section = ReceiptSection(
        receipt_id=1,
        image_id=VALID_IMAGE_ID,
        section_type="SUMMARY",
        line_ids=[4],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        model_source="section-seed-v0",
        validation_status="VALID",
    )
    client = _StubDynamoClient(
        sections=[_items_section(line_ids=(1, 2)), summary_section]
    )

    result = asyncio.run(
        module.extend_items_section_impl(client, VALID_IMAGE_ID, 1, [4])
    )
    assert "error" in result
    assert "SUMMARY" in result["error"]
    assert client.updated_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_refuses_without_summary_baseline(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(
        sections=[_items_section(line_ids=(1, 2))], summary_record=None
    )

    result = asyncio.run(
        module.extend_items_section_impl(client, VALID_IMAGE_ID, 1, [3])
    )
    assert "error" in result
    assert "baseline" in result["error"].lower()
    assert client.updated_sections == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_extend_rejects_unknown_line_ids(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    client = _StubDynamoClient(sections=[_items_section(line_ids=(1, 2))])

    result = asyncio.run(
        module.extend_items_section_impl(client, VALID_IMAGE_ID, 1, [99])
    )
    assert "error" in result
    assert "99" in result["error"]
    assert client.updated_sections == []


# ---------------------------------------------------------------------------
# list_reconciliation_worklist_impl
# ---------------------------------------------------------------------------


class _WorklistClient:
    """Stub with paginated line items + per-receipt summaries."""

    def __init__(self, pages, summaries):
        self._pages = pages
        self._summaries = summaries
        self.summary_calls = 0

    def list_receipt_line_items(self, limit=None, last_evaluated_key=None):
        idx = 0 if last_evaluated_key is None else last_evaluated_key["page"]
        items = self._pages[idx]
        next_key = {"page": idx + 1} if idx + 1 < len(self._pages) else None
        return items, next_key

    def get_receipt_summary(self, image_id, receipt_id):
        self.summary_calls += 1
        key = (image_id, receipt_id)
        if key not in self._summaries:
            from receipt_dynamo.data.shared_exceptions import (
                EntityNotFoundError,
            )

            raise EntityNotFoundError("summary not found")
        subtotal = self._summaries[key]
        return SimpleNamespace(subtotal=subtotal, grand_total=None, tax=None)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_worklist_groups_filters_and_sorts_by_abs_delta(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])

    pages = [
        [
            # Receipt A: mismatch, delta -4.00
            _line_item(0, "APPLES", "3.00", image_id=VALID_IMAGE_ID),
            _line_item(1, "BANANAS", "2.00", image_id=VALID_IMAGE_ID),
        ],
        [
            # Receipt B (other merchant): mismatch, delta -90.00
            _line_item(
                0,
                "WIDGET",
                "10.00",
                image_id=OTHER_IMAGE_ID,
                merchant_name="Mega Store",
            ),
            # Receipt B second page item; discount must not join the sum
            _line_item(
                1,
                "COUPON",
                "-5.00",
                is_discount=True,
                image_id=OTHER_IMAGE_ID,
                merchant_name="Mega Store",
            ),
            # Receipt C: all items match — filtered out for "mismatch"
            _line_item(
                0,
                "CLEAN",
                "7.00",
                reconciliation_status="match",
                image_id=VALID_IMAGE_ID,
                receipt_id=2,
            ),
        ],
    ]
    summaries = {
        (VALID_IMAGE_ID, 1): 9.00,
        (OTHER_IMAGE_ID, 1): 100.00,
        (VALID_IMAGE_ID, 2): 7.00,
    }
    client = _WorklistClient(pages, summaries)

    result = asyncio.run(
        module.list_reconciliation_worklist_impl(client, status="mismatch")
    )

    assert "error" not in result
    assert result["receipts_scanned"] == 3
    assert result["matching"] == 2
    worklist = result["worklist"]
    # Sorted by |delta| descending: B (-90.00) before A (-4.00).
    assert [w["image_id"] for w in worklist] == [
        OTHER_IMAGE_ID,
        VALID_IMAGE_ID,
    ]
    b, a = worklist
    assert b["merchant"] == "Mega Store"
    assert b["items"] == 2
    assert b["items_sum"] == 10.00  # discount excluded
    assert b["subtotal"] == 100.00
    assert b["delta"] == -90.00
    assert a["delta"] == -4.00
    # Receipt C matched — its summary is never fetched.
    assert client.summary_calls == 2


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_worklist_merchant_filter_is_case_insensitive_substring(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    pages = [
        [
            _line_item(0, "APPLES", "3.00", image_id=VALID_IMAGE_ID),
            _line_item(
                0,
                "WIDGET",
                "10.00",
                image_id=OTHER_IMAGE_ID,
                merchant_name="Mega Store",
            ),
        ]
    ]
    client = _WorklistClient(
        pages,
        {(VALID_IMAGE_ID, 1): 9.00, (OTHER_IMAGE_ID, 1): 100.00},
    )

    result = asyncio.run(
        module.list_reconciliation_worklist_impl(
            client, merchant_name="mega", status="mismatch"
        )
    )
    assert result["matching"] == 1
    assert result["worklist"][0]["merchant"] == "Mega Store"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_worklist_rejects_invalid_status(label):
    module = _load_module(label, SERVER_FILES[label])

    class _Exploding:
        def __getattr__(self, name):
            raise AssertionError("client must not be called")

    result = asyncio.run(
        module.list_reconciliation_worklist_impl(_Exploding(), status="bogus")
    )
    assert "error" in result
    assert "bogus" in result["error"]


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_worklist_missing_summary_sorts_last(label):
    pytest.importorskip("receipt_dynamo")
    module = _load_module(label, SERVER_FILES[label])
    pages = [
        [
            _line_item(0, "APPLES", "3.00", image_id=VALID_IMAGE_ID),
            _line_item(0, "GHOST", "1.00", image_id=OTHER_IMAGE_ID),
        ]
    ]
    client = _WorklistClient(pages, {(VALID_IMAGE_ID, 1): 9.00})

    result = asyncio.run(
        module.list_reconciliation_worklist_impl(client, status="mismatch")
    )
    worklist = result["worklist"]
    assert len(worklist) == 2
    assert worklist[0]["image_id"] == VALID_IMAGE_ID
    assert worklist[1]["subtotal"] is None
    assert worklist[1]["delta"] is None

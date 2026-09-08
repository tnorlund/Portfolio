"""get_receipt amounts extraction on both MCP servers.

Two gaps found in the 2026-08-10 receipt audit, pinned here for both
the stdio server (``scripts/receipt_mcp_server.py``) and the Lambda
copy (``infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py``):

1. Trailing-minus accounting negatives -- Target return receipts print
   refunds as "$16.25-" (dev receipt
   d30ba860-4bd6-4c9e-a6d7-c2eaed0c2149 receipt 1 prints "$16.25-" /
   "$14.99-" / "$1.26-" with correct VALID labels). The old
   ``float(text.replace("$", ""))`` parse raised ValueError on every
   one, so the receipt's ``amounts`` came back EMPTY and refunds were
   invisible to spend aggregation.
2. TIP was missing from the money-label list, so a VALID TIP label
   (dev receipt 29a3b291-ac80-47bf-bdc9-2cde1d79f1b6, TIP $2.00) never
   surfaced in ``amounts`` at all.
"""

import asyncio
import importlib.util
import sys
import types
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

RETURN_IMAGE_ID = "d30ba860-4bd6-4c9e-a6d7-c2eaed0c2149"
TIP_IMAGE_ID = "29a3b291-ac80-47bf-bdc9-2cde1d79f1b6"


class _FakeTool:
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
        f"receipt_mcp_server_amounts_{label}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _word(line_id, word_id, text, x, y):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.1, "height": 0.012},
        calculate_centroid=lambda x=x, y=y: (x, y),
    )


def _valid_label(line_id, word_id, label):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status="VALID",
        timestamp_added="2026-08-10T00:00:00+00:00",
    )


class _StubDynamo:
    def __init__(self, words, labels, merchant):
        self._details = SimpleNamespace(
            words=words,
            labels=labels,
            place=SimpleNamespace(merchant_name=merchant),
        )

    def get_receipt_details(self, image_id, receipt_id):
        return self._details


def _target_return_client():
    """Target return d30ba860 r1: every summary figure prints 'N.NN-'."""
    words = [
        _word(10, 1, "SUBTOTAL", 0.1, 0.34),
        _word(10, 2, "$14.99-", 0.8, 0.34),
        _word(11, 1, "TAX", 0.1, 0.32),
        _word(11, 2, "$1.26-", 0.8, 0.32),
        _word(12, 1, "TOTAL", 0.1, 0.30),
        _word(12, 2, "$16.25-", 0.8, 0.30),
    ]
    labels = [
        _valid_label(10, 2, "SUBTOTAL"),
        _valid_label(11, 2, "TAX"),
        _valid_label(12, 2, "GRAND_TOTAL"),
    ]
    return _StubDynamo(words, labels, "Target")


def _tip_client():
    """29a3b291: a VALID TIP $2.00 that must surface in amounts."""
    words = [
        _word(20, 1, "Tip:", 0.1, 0.30),
        _word(20, 2, "$2.00", 0.8, 0.30),
        _word(21, 1, "Total:", 0.1, 0.28),
        _word(21, 2, "$12.00", 0.8, 0.28),
    ]
    labels = [
        _valid_label(20, 2, "TIP"),
        _valid_label(21, 2, "GRAND_TOTAL"),
    ]
    return _StubDynamo(words, labels, "Some Restaurant")


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_trailing_minus_amounts_parse_negative(label):
    module = _load_module(label, SERVER_FILES[label])
    result = asyncio.run(
        module.get_receipt_impl(_target_return_client(), RETURN_IMAGE_ID, 1)
    )

    assert "error" not in result
    amounts = {a["label"]: a["amount"] for a in result["amounts"]}
    # The audit found this receipt's amounts EMPTY; all three refund
    # figures must now parse, with their accounting sign intact.
    assert amounts == {
        "GRAND_TOTAL": pytest.approx(-16.25),
        "SUBTOTAL": pytest.approx(-14.99),
        "TAX": pytest.approx(-1.26),
    }
    # Negative reconciliation: the refund's own arithmetic holds.
    assert amounts["SUBTOTAL"] + amounts["TAX"] == pytest.approx(
        amounts["GRAND_TOTAL"]
    )


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_tip_label_surfaces_in_amounts(label):
    module = _load_module(label, SERVER_FILES[label])
    result = asyncio.run(
        module.get_receipt_impl(_tip_client(), TIP_IMAGE_ID, 1)
    )

    assert "error" not in result
    amounts = {a["label"]: a["amount"] for a in result["amounts"]}
    assert amounts["TIP"] == pytest.approx(2.00)
    assert amounts["GRAND_TOTAL"] == pytest.approx(12.00)

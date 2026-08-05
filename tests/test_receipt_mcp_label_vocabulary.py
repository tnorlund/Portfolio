"""``create_word_label`` must not be able to mint a new label type.

A word label's name is part of its DynamoDB sort key, so every distinct
string a caller passes becomes a new pseudo-label-type. Production carries
394 rows across 72 malformed label strings from a since-fixed free-text
parser (#758); the MCP ``create_word_label`` tool was the remaining
structurally-open writer.

These tests exercise the real tool path on BOTH server copies -- the stdio
``scripts/receipt_mcp_server.py`` and the deployed Lambda
``infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`` -- which
must stay identical. They assert:

* the published ``inputSchema`` declares the allowed values as an ``enum``,
* ``create_word_label_impl`` refuses free text *before* touching DynamoDB,
* a known soft alias is normalised and written,
* ``update_word_label_impl`` still works on a stored malformed row, i.e.
  the guard is on the write path only and never on a read.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from receipt_dynamo.constants import CORE_LABELS

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

VALID_IMAGE_ID = "344f4a1b-1476-442e-bb01-7eed30934285"

# Real malformed label strings from the production corpus.
JUNK_LABELS = [
    "SUBTOTAL SHOULD BE $214.46 (OR THE $4014.97 ENTRY SHOULD BE "
    "CORRECTED/REMOVED)",
    "LINE_TOTAL (SHOULD BE 69.60)",
    "7829.53",
    "grand total",
]

MALFORMED_STORED_LABEL = "LINE_TOTAL (SHOULD BE 69.60)"


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
        f"receipt_mcp_server_vocab_{label}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_word_label_schema(module):
    tools = asyncio.run(module.list_tools())
    by_name = {t.name: t for t in tools}
    return by_name["create_word_label"].inputSchema


class _RecordingDynamoClient:
    """Records writes; raises if the impl ever reaches DynamoDB unexpectedly.

    ``add_receipt_word_label`` mirrors the real guard so this test proves the
    tool boundary refuses first (``added`` stays empty and no exception is
    raised), not that the deeper guard happens to catch it.
    """

    def __init__(self, existing=None):
        self.added = []
        self.updated = []
        self.existing = existing or {}

    def add_receipt_word_label(self, label, **kwargs):
        if label.label not in CORE_LABELS and not kwargs.get(
            "allow_non_core_labels"
        ):
            raise AssertionError(
                "non-core label reached DynamoDB: " + repr(label.label)
            )
        self.added.append(label)

    def get_receipt_word_label(
        self, image_id, receipt_id, line_id, word_id, label
    ):
        return self.existing[label]

    def update_receipt_word_label(self, label):
        self.updated.append(label)


def _stored_label(label):
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    return ReceiptWordLabel(
        image_id=VALID_IMAGE_ID,
        receipt_id=1,
        line_id=10,
        word_id=1,
        label=label,
        reasoning="written 2026-01-18 by the label-evaluator free-text parser",
        timestamp_added="2026-01-18T17:12:51.520489+00:00",
        validation_status="VALID",
        label_proposed_by="label-evaluator-llm",
    )


def _create(module, client, label, **overrides):
    kwargs = {
        "image_id": VALID_IMAGE_ID,
        "receipt_id": 1,
        "line_id": 10,
        "word_id": 1,
        "label": label,
        "reasoning": "test",
    }
    kwargs.update(overrides)
    return asyncio.run(module.create_word_label_impl(client, **kwargs))


# ---------------------------------------------------------------------------
# Tool boundary: the schema declares the vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_create_word_label_schema_declares_the_enum(server):
    schema = _create_word_label_schema(
        _load_module(server, SERVER_FILES[server])
    )
    label_schema = schema["properties"]["label"]
    assert label_schema["type"] == "string"
    assert set(label_schema["enum"]) == set(CORE_LABELS)
    assert label_schema["enum"] == sorted(CORE_LABELS)


def test_both_servers_publish_the_same_create_word_label_schema():
    stdio = _load_module("stdio-vocab", SERVER_FILES["stdio"])
    lam = _load_module("lambda-vocab", SERVER_FILES["lambda"])
    assert _create_word_label_schema(stdio) == _create_word_label_schema(lam)


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_update_word_label_schema_is_not_enum_constrained(server):
    """update targets an EXISTING row; constraining it would make the 394
    malformed rows untouchable by the tool needed to triage them."""
    module = _load_module(server, SERVER_FILES[server])
    tools = asyncio.run(module.list_tools())
    schema = {t.name: t.inputSchema for t in tools}["update_word_label"]
    assert "enum" not in schema["properties"]["label"]


# ---------------------------------------------------------------------------
# WRITE path: junk is refused at the tool boundary, before any DynamoDB call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
@pytest.mark.parametrize("junk", JUNK_LABELS)
def test_create_word_label_refuses_junk(server, junk):
    module = _load_module(server, SERVER_FILES[server])
    client = _RecordingDynamoClient()

    result = _create(module, client, junk)

    assert "error" in result, result
    assert "label must be one of" in result["error"]
    assert "GRAND_TOTAL" in result["error"]
    assert result.get("success") is None
    # Nothing was written, and the refusal happened before DynamoDB.
    assert client.added == []


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_create_word_label_writes_a_core_label(server):
    module = _load_module(server, SERVER_FILES[server])
    client = _RecordingDynamoClient()

    result = _create(module, client, "cash_back")

    assert result["success"] is True
    assert result["label"] == "CASH_BACK"
    assert [lbl.label for lbl in client.added] == ["CASH_BACK"]
    assert client.added[0].key["SK"]["S"].endswith("#LABEL#CASH_BACK")


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_create_word_label_normalises_a_known_alias(server):
    module = _load_module(server, SERVER_FILES[server])
    client = _RecordingDynamoClient()

    result = _create(module, client, "ADDRESS")

    assert result["success"] is True
    assert result["label"] == "ADDRESS_LINE"
    assert [lbl.label for lbl in client.added] == ["ADDRESS_LINE"]


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_create_word_label_error_suggests_an_alias_target(server):
    module = _load_module(server, SERVER_FILES[server])
    client = _RecordingDynamoClient()

    result = _create(module, client, "card number")

    # "CARD NUMBER" is not the "CARD_NUMBER" alias -- it is refused outright.
    assert "label must be one of" in result["error"]
    assert client.added == []


# ---------------------------------------------------------------------------
# READ path: the stored malformed corpus stays usable through the tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_update_word_label_still_works_on_a_stored_malformed_row(server):
    """The guard must not fire on a read-modify-write of a legacy row."""
    module = _load_module(server, SERVER_FILES[server])
    client = _RecordingDynamoClient(
        existing={
            MALFORMED_STORED_LABEL: _stored_label(MALFORMED_STORED_LABEL)
        }
    )

    result = asyncio.run(
        module.update_word_label_impl(
            client,
            image_id=VALID_IMAGE_ID,
            receipt_id=1,
            line_id=10,
            word_id=1,
            label=MALFORMED_STORED_LABEL,
            new_status="INVALID",
            reasoning="triage: malformed label name",
        )
    )

    assert result.get("success") is True, result
    assert result["label"] == MALFORMED_STORED_LABEL
    assert [lbl.label for lbl in client.updated] == [MALFORMED_STORED_LABEL]


@pytest.mark.parametrize("server", sorted(SERVER_FILES))
def test_no_impl_reconstructs_a_read_label_through_the_guard(server):
    """create_word_label is the only label-minting tool on either server."""
    module = _load_module(server, SERVER_FILES[server])
    tools = asyncio.run(module.list_tools())
    minting = {
        t.name
        for t in tools
        if "label" in t.inputSchema.get("properties", {})
        and "enum" in t.inputSchema["properties"]["label"]
    }
    assert minting == {"create_word_label"}


def _fake_ns(**kwargs):  # pragma: no cover - helper kept for readability
    return SimpleNamespace(**kwargs)

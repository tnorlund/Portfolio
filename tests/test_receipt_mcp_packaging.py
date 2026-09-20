"""Exercise shared-server startup and the Lambda adapter without services."""

import asyncio
import json
import runpy
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from receipt_mcp_test_support import (
    REPO_ROOT,
    SERVER_FILES,
    load_server_module,
    stage_lambda_files,
)
from test_receipt_mcp_section_tools import _load_module

ADAPTER = (
    REPO_ROOT / "infra/mcp_server_lambda/lambdas/receipt_mcp_server_init.py"
)


def test_lambda_entry_point_passes_explicit_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage_lambda_files(tmp_path)
    adapter = load_server_module("receipt_mcp_server", ADAPTER)
    server = ModuleType("receipt_mcp_server.server")
    run = AsyncMock()
    monkeypatch.setattr(server, "main", run, raising=False)
    monkeypatch.setitem(sys.modules, "receipt_mcp_server", adapter)
    monkeypatch.setitem(sys.modules, "receipt_mcp_server.server", server)
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runpy.run_path(
        str(tmp_path / "receipt_mcp_server/__main__.py"), run_name="__main__"
    )

    run.assert_awaited_once_with(
        config=adapter.load_config(), model_activator=adapter.set_active_model
    )
    assert run.call_args.kwargs["config"]["dynamodb_table_name"] == (
        "ReceiptsTable-test"
    )
    assert run.call_args.kwargs["config"]["openai_api_key"] == "test-key"


def test_lambda_config_uses_only_present_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_server_module("receipt_mcp_adapter", ADAPTER)
    for name in (
        "DYNAMODB_TABLE_NAME",
        "PORTFOLIO_ENV",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "LANGCHAIN_API_KEY",
        "GOOGLE_PLACES_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert adapter.load_config() == {}
    monkeypatch.setenv("PORTFOLIO_ENV", "dev")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "places-test")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert adapter.load_config() == {
        "portfolio_env": "dev",
        "google_places_api_key": "places-test",
    }


def test_local_config_retains_pulumi_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module("local-config", SERVER_FILES["stdio"])
    pulumi = ModuleType("receipt_dynamo.data._pulumi")
    load_env = Mock(return_value={"dynamodb_table_name": "ReceiptsTable-test"})
    load_secrets = Mock(return_value={"portfolio:OPENAI_API_KEY": "test-key"})
    monkeypatch.setattr(pulumi, "load_env", load_env, raising=False)
    monkeypatch.setattr(pulumi, "load_secrets", load_secrets, raising=False)
    monkeypatch.setitem(sys.modules, "receipt_dynamo.data._pulumi", pulumi)
    monkeypatch.setenv("PORTFOLIO_ENV", "dev")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ignored-by-local-entry-point")
    monkeypatch.delenv("RECEIPT_AGENT_OPENAI_API_KEY", raising=False)

    config = module._load_config()
    assert config == {
        "dynamodb_table_name": "ReceiptsTable-test",
        "openai_api_key": "test-key",
    }
    assert module._load_config() is config
    load_env.assert_called_once_with(env="dev")
    load_secrets.assert_called_once_with(env="dev")


def test_lambda_startup_routes_activation_without_pulumi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module("lambda-config", SERVER_FILES["lambda"])
    config = {
        "dynamodb_table_name": "ReceiptsTable-test",
        "openai_api_key": "test-key",
    }
    client = Mock()
    activate = AsyncMock(return_value={"success": True, "name": "selected"})
    monkeypatch.setattr(module, "get_dynamo_client", lambda: client)
    monkeypatch.delenv("RECEIPT_AGENT_OPENAI_API_KEY", raising=False)

    @asynccontextmanager
    async def streams() -> AsyncIterator[tuple[None, None]]:
        yield None, None

    registered: list[Any] = []

    async def serve(*_args: Any) -> None:
        assert module._load_config() == config
        content = await registered[0](
            "set_active_model", {"job_name": "selected"}
        )
        assert json.loads(content[0].text) == {
            "success": True,
            "name": "selected",
        }

    monkeypatch.setattr(module, "stdio_server", streams)
    monkeypatch.setattr(module.server, "call_tool", lambda: registered.append)
    monkeypatch.setattr(module.server, "run", serve, raising=False)
    monkeypatch.setattr(
        module.server,
        "create_initialization_options",
        lambda: None,
        raising=False,
    )
    asyncio.run(module.main(config=config, model_activator=activate))
    activate.assert_awaited_once_with(client, "selected")


def test_lambda_preserves_tag_activation_without_cli_bundle_promotion() -> (
    None
):
    adapter = load_server_module("receipt_mcp_adapter", ADAPTER)
    server = _load_module("local-activation", SERVER_FILES["stdio"])
    selected = SimpleNamespace(
        name="selected",
        job_id="new",
        tags={"keep": "new"},
        results={"best_f1": 0.9},
    )
    previous = SimpleNamespace(
        name="previous",
        job_id="old",
        tags={"active_model": "true", "keep": "old"},
    )
    client = Mock()
    client.get_job_by_name.return_value = ([selected], None)
    client.get_active_model_job.return_value = previous

    # The local implementation must still refuse a model with no exported
    # bundle before writing tags. Lambda's established capability is tag-only.
    result = asyncio.run(server.set_active_model_impl(client, "selected"))
    assert result["success"] is False
    assert "no exported CoreML bundle" in result["error"]
    client.update_job.assert_not_called()

    result = asyncio.run(adapter.set_active_model(client, "selected"))
    assert result == {
        "success": True,
        "name": "selected",
        "job_id": "new",
        "best_f1": 0.9,
        "message": "Set selected as the active model",
    }
    assert previous.tags == {"keep": "old"}
    assert selected.tags == {"keep": "new", "active_model": "true"}
    assert [call.args[0] for call in client.update_job.call_args_list] == [
        previous,
        selected,
    ]


@pytest.mark.parametrize("failure", ["missing", "lookup"])
def test_lambda_activation_keeps_error_response_without_writes(
    failure: str,
) -> None:
    adapter = load_server_module("receipt_mcp_adapter", ADAPTER)
    client = Mock()
    client.get_job_by_name.return_value = ([], None)
    if failure == "lookup":
        client.get_job_by_name.side_effect = RuntimeError("lookup failed")
    result = asyncio.run(adapter.set_active_model(client, "selected"))
    assert result == {
        "error": (
            "lookup failed"
            if failure == "lookup"
            else "No job found with name: selected"
        )
    }
    client.update_job.assert_not_called()

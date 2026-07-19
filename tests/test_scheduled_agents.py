"""Tests for reviewable Claude Routine configuration."""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_PATH = REPO_ROOT / "scripts" / "sync_scheduled_agents.py"
SPEC = importlib.util.spec_from_file_location("sync_scheduled_agents", SYNC_PATH)
assert SPEC and SPEC.loader
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


def test_receipt_label_fixer_uses_connected_connector_by_name() -> None:
    path = REPO_ROOT / "infra" / "scheduled_agents" / "receipt_label_fixer.json"
    config = SYNC.load_agent_config(path)

    assert config["required_connectors"] == ["receipt-tools-dev"]
    assert "mcp_connector" not in config
    serialized = json.dumps(config)
    assert "client_secret" not in serialized
    assert "access_token" not in serialized
    assert "${MCP_SERVER_URL}" not in serialized


def test_loader_rejects_raw_connector_urls(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(
        json.dumps(
            {
                "name": "unsafe",
                "cron_expression": "0 6 * * *",
                "prompt": "unsafe",
                "required_connectors": ["receipt-tools-dev"],
                "mcp_connector": {
                    "name": "receipt-tools-dev",
                    "url": "https://example.test/mcp",
                },
            }
        )
    )

    with pytest.raises(ValueError, match="not raw URLs"):
        SYNC.load_agent_config(path)

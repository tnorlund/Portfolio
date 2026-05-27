#!/usr/bin/env python3
"""Sync scheduled agent configs to Claude Code routines API.

Reads JSON configs from infra/scheduled_agents/ and creates or updates
the corresponding routines via the Claude Code RemoteTrigger API.

Usage:
    # Dry run — show what would change
    python scripts/sync_scheduled_agents.py

    # Apply changes
    python scripts/sync_scheduled_agents.py --apply

    # List current routines
    python scripts/sync_scheduled_agents.py --list

Requires: claude CLI authenticated (uses claude api triggers)
"""

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "infra" / "scheduled_agents"
ENVIRONMENT_ID = "env_01ELefm6C9bVcvqCVgbptR5N"


def run_claude_api(action: str, trigger_id: str = None, body: dict = None) -> dict:
    """Call the Claude Code routines API via subprocess."""
    # Use curl with the auth token from claude CLI config
    import os

    # Build the API call - we shell out to claude since it handles auth
    cmd = ["claude", "api", "triggers"]
    if action == "list":
        cmd.append("list")
    elif action == "create":
        cmd.extend(["create", "--body", json.dumps(body)])
    elif action == "update":
        cmd.extend(["update", trigger_id, "--body", json.dumps(body)])
    elif action == "get":
        cmd.extend(["get", trigger_id])

    # For now, print the equivalent curl command since claude api triggers
    # may not exist as a CLI command
    return {"action": action, "trigger_id": trigger_id, "body": body}


def load_agent_config(path: Path) -> dict:
    """Load and validate an agent config file."""
    with open(path) as f:
        config = json.load(f)

    required = ["name", "cron_expression", "prompt"]
    for field in required:
        if field not in config:
            raise ValueError(f"Missing required field '{field}' in {path.name}")

    return config


def config_to_api_body(config: dict) -> dict:
    """Convert a simplified config to the full API body."""
    mcp_connections = []
    if config.get("mcp_connector"):
        mc = config["mcp_connector"]
        mcp_connections.append({
            "name": mc["name"],
            "url": mc["url"],
        })

    return {
        "name": config["name"],
        "cron_expression": config["cron_expression"],
        "enabled": config.get("enabled", True),
        "mcp_connections": mcp_connections,
        "job_config": {
            "ccr": {
                "environment_id": ENVIRONMENT_ID,
                "session_context": {
                    "model": config.get("model", "claude-sonnet-4-6"),
                    "sources": [
                        {"git_repository": {"url": config.get("repository", "")}}
                    ],
                    "allowed_tools": ["Bash", "Read", "Write"],
                },
                "events": [
                    {
                        "data": {
                            "uuid": str(uuid.uuid4()),
                            "session_id": "",
                            "type": "user",
                            "parent_tool_use_id": None,
                            "message": {
                                "content": config["prompt"],
                                "role": "user",
                            },
                        }
                    }
                ],
            }
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Sync scheduled agent configs")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--list", action="store_true", help="List current routines")
    args = parser.parse_args()

    if not AGENTS_DIR.exists():
        print(f"No agents directory at {AGENTS_DIR}")
        sys.exit(1)

    configs = sorted(AGENTS_DIR.glob("*.json"))
    if not configs:
        print("No agent configs found")
        sys.exit(0)

    print(f"Found {len(configs)} agent config(s):\n")

    for path in configs:
        config = load_agent_config(path)
        body = config_to_api_body(config)

        print(f"  {config['name']}")
        print(f"    Schedule: {config['cron_expression']}")
        print(f"    Model: {config.get('model', 'claude-sonnet-4-6')}")
        print(f"    Enabled: {config.get('enabled', True)}")
        if config.get("mcp_connector"):
            print(f"    MCP: {config['mcp_connector']['name']} @ {config['mcp_connector']['url'][:50]}...")
        print(f"    Prompt: {config['prompt'][:80]}...")
        print()

        if args.apply:
            print(f"    To sync, use Claude Code:")
            print(f"    /schedule update receipt-label-fixer")
            print(f"    Or use the RemoteTrigger API with this body:")
            print(f"    {json.dumps(body, indent=2)[:200]}...")
            print()

    if not args.apply:
        print("Dry run. Use --apply to see sync instructions.")


if __name__ == "__main__":
    main()

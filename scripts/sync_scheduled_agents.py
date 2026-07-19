#!/usr/bin/env python3
"""Validate scheduled agent configs and print Claude Routine setup steps.

Claude stores OAuth connector grants in the user's account. They cannot be
created safely from repository JSON, so this script deliberately records only
the required connected-connector names and never serializes connector URLs,
client secrets, or bearer tokens.

Usage:
    # Dry run — show what would change
    python scripts/sync_scheduled_agents.py

    # Print the account-level update steps
    python scripts/sync_scheduled_agents.py --apply

    # List current routines
    python scripts/sync_scheduled_agents.py --list

Requires: an authenticated Claude account with the named connectors connected
"""

import argparse
import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "infra" / "scheduled_agents"


def load_agent_config(path: Path) -> dict:
    """Load and validate an agent config file."""
    with open(path) as f:
        config = json.load(f)

    required = ["name", "cron_expression", "prompt", "required_connectors"]
    for field in required:
        if field not in config:
            raise ValueError(f"Missing required field '{field}' in {path.name}")

    connectors = config["required_connectors"]
    if not isinstance(connectors, list) or not connectors:
        raise ValueError(f"required_connectors must be a non-empty list in {path.name}")
    if not all(isinstance(name, str) and name.strip() for name in connectors):
        raise ValueError(
            f"required_connectors must contain connector names in {path.name}"
        )
    if "mcp_connector" in config:
        raise ValueError(
            f"{path.name} must reference account connectors by name, not raw URLs"
        )
    return config


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
        print(f"  {config['name']}")
        print(f"    Schedule: {config['cron_expression']}")
        print(f"    Model: {config.get('model', 'claude-sonnet-4-6')}")
        print(f"    Enabled: {config.get('enabled', True)}")
        print("    Connectors: " + ", ".join(config["required_connectors"]))
        print(f"    Prompt: {config['prompt'][:80]}...")
        print()

        if args.apply:
            print("    Update this account-owned Routine with:")
            print(f"    /schedule update {config['name']}")
            print("    Attach these already-connected account connectors:")
            for connector in config["required_connectors"]:
                print(f"      - {connector}")
            print("    Then copy the checked-in prompt into the Routine.")
            print()

    if not args.apply:
        print("Validated. Use --apply to print account-level update steps.")


if __name__ == "__main__":
    main()

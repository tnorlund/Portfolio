"""Configuration loading for MCP server."""

import os
import json
import subprocess
import shutil
from pathlib import Path


def load_pulumi_config() -> dict:
    """Load configuration from Pulumi stack."""
    # Find pulumi executable
    pulumi_path = shutil.which("pulumi")
    if not pulumi_path:
        # Try common locations
        for common_path in [
            "/Users/tnorlund/.pulumi/bin/pulumi",
            "/usr/local/bin/pulumi",
            "/opt/homebrew/bin/pulumi",
        ]:
            if os.path.exists(common_path):
                pulumi_path = common_path
                break
        else:
            raise RuntimeError(
                "Pulumi executable not found. Please ensure Pulumi is installed and in PATH."
            )

    try:
        # Calculate infra path more explicitly
        server_file = Path(__file__).resolve()  # Get absolute path

        # Expected structure: /Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_isolated/core/config.py
        # Navigate up: core -> mcp_isolated -> receipt_label -> Portfolio -> infra
        infra_path = server_file.parent.parent.parent.parent / "infra"

        # Debug: print the paths to understand what's happening
        if not infra_path.exists():
            # Try alternative paths
            possible_paths = [
                server_file.parent.parent.parent.parent / "infra",  # Portfolio/infra
                server_file.parent.parent.parent.parent.parent / "infra",  # claude_b/infra
                Path("/Users/tnorlund/claude_b/Portfolio/infra"),  # Absolute fallback
            ]

            for path in possible_paths:
                if path.exists():
                    infra_path = path
                    break
            else:
                raise RuntimeError(
                    f"Infra directory not found. Tried:\n"
                    + "\n".join([f"  - {p}" for p in possible_paths])
                    + f"\nConfig file: {server_file}"
                )

        # Select stack
        result = subprocess.run(
            [pulumi_path, "stack", "select", "tnorlund/portfolio/dev"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to select Pulumi stack: {result.stderr}")

        # Get outputs
        result = subprocess.run(
            [pulumi_path, "stack", "output", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get Pulumi outputs: {result.stderr}")

        outputs = json.loads(result.stdout) if result.stdout else {}

        # Get config
        result = subprocess.run(
            [pulumi_path, "config", "--show-secrets", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get Pulumi config: {result.stderr}")

        secrets = json.loads(result.stdout) if result.stdout else {}

        # Build config and set env vars
        config = {
            "dynamo_table": outputs.get("dynamodb_table_name")
            or outputs.get("dynamoTableName"),
            "openai_key": secrets.get("portfolio:OPENAI_API_KEY", {}).get("value"),
            "pinecone_key": secrets.get("portfolio:PINECONE_API_KEY", {}).get("value"),
            "pinecone_index": secrets.get("portfolio:PINECONE_INDEX_NAME", {}).get("value"),
            "pinecone_host": secrets.get("portfolio:PINECONE_HOST", {}).get("value"),
        }

        # Validate we got the essential config
        if not config.get("dynamo_table"):
            raise RuntimeError("Failed to get DynamoDB table name from Pulumi")

        # Set environment variables for ClientManager
        for key, env_var in [
            ("openai_key", "OPENAI_API_KEY"),
            ("pinecone_key", "PINECONE_API_KEY"),
            ("pinecone_index", "PINECONE_INDEX_NAME"),
            ("pinecone_host", "PINECONE_HOST"),
            ("dynamo_table", "DYNAMODB_TABLE_NAME"),
        ]:
            if config.get(key):
                os.environ[env_var] = config[key]

        return config

    except subprocess.SubprocessError as e:
        raise RuntimeError(f"Subprocess error running Pulumi: {str(e)}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Pulumi JSON output: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Failed to load Pulumi config: {str(e)}")
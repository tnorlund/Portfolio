import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Optional


def load_env(
    env: str = "dev", project_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Retrieves Pulumi stack outputs for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack outputs.
    """
    try:
        cmd: list[str] = ["pulumi"]
        if project_dir is not None:
            cmd.extend(["--cwd", str(project_dir)])
        cmd.extend(
            [
                "stack",
                "output",
                "--stack",
                f"tnorlund/portfolio/{env}",
                "--json",
            ]
        )
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        result_data = json.loads(result.stdout)
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure


def load_secrets(
    env: str = "dev", project_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Retrieves Pulumi stack secrets for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack secrets.
    """
    try:
        cmd: list[str] = ["pulumi"]
        if project_dir is not None:
            cmd.extend(["--cwd", str(project_dir)])
        cmd.extend(
            [
                "config",
                "--show-secrets",
                "--stack",
                f"tnorlund/portfolio/{env}",
                "--json",
            ]
        )
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        result_data = json.loads(result.stdout)
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure

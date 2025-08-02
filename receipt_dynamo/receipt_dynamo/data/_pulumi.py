import json
import subprocess
from typing import Any, Dict


def load_env(env: str = "dev") -> Dict[str, Any]:
    """Retrieves Pulumi stack outputs for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack outputs.
    """
    try:
        result = subprocess.run(
            [
                "pulumi",
                "stack",
                "output",
                "--stack",
                f"tnorlund/portfolio/{env}",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result_data = json.loads(result.stdout)
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure


def load_secrets(env: str = "dev") -> Dict[str, Any]:
    """Retrieves Pulumi stack secrets for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack secrets.
    """
    try:
        result = subprocess.run(
            [
                "pulumi",
                "config",
                "--show-secrets",
                "--stack",
                f"tnorlund/portfolio/{env}",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result_data = json.loads(result.stdout)
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure

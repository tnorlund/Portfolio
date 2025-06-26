import json
import subprocess


def load_env(env: str = "dev") -> dict:
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
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure


def load_secrets(env: str = "dev") -> dict:
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
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure

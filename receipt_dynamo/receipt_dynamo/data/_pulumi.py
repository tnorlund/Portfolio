import json
import subprocess
from typing import Any, Dict


def load_env(env: str = "dev", working_dir: str = None) -> Dict[str, Any]:
    """Retrieves Pulumi stack outputs for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".
        working_dir (str, optional): Directory containing Pulumi.yaml.
            If None, auto-detects from current working directory.

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack outputs.
    """
    from pathlib import Path
    
    try:
        # Find the directory containing Pulumi.yaml
        if working_dir:
            pulumi_dir = Path(working_dir)
        else:
            # Try to find infra directory relative to this file
            infra_dir = Path(__file__).parent.parent.parent.parent / "infra"
            if infra_dir.exists() and (infra_dir / "Pulumi.yaml").exists():
                pulumi_dir = infra_dir
            else:
                # Fall back to current working directory
                pulumi_dir = Path.cwd()
        
        if not pulumi_dir.exists() or not (pulumi_dir / "Pulumi.yaml").exists():
            return {}
            
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
            cwd=str(pulumi_dir),
        )
        result_data = json.loads(result.stdout)
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}  # Return an empty dictionary on failure


def load_secrets(env: str = "dev", working_dir: str = None) -> Dict[str, Any]:
    """Retrieves Pulumi stack secrets for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or
            "prod". Defaults to "dev".
        working_dir (str, optional): Directory containing Pulumi.yaml.
            If None, auto-detects from current working directory.

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack secrets.
    """
    from pathlib import Path
    
    try:
        # Find the directory containing Pulumi.yaml
        if working_dir:
            pulumi_dir = Path(working_dir)
        else:
            # Try to find infra directory relative to this file
            infra_dir = Path(__file__).parent.parent.parent.parent / "infra"
            if infra_dir.exists() and (infra_dir / "Pulumi.yaml").exists():
                pulumi_dir = infra_dir
            else:
                # Fall back to current working directory
                pulumi_dir = Path.cwd()
        
        if not pulumi_dir.exists() or not (pulumi_dir / "Pulumi.yaml").exists():
            return {}
            
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
            cwd=str(pulumi_dir),
        )
        result_data = json.loads(result.stdout)
        
        # Extract the actual values from the Pulumi config format
        # Format: {"key": {"value": "actual-value", "secret": true}}
        if isinstance(result_data, dict):
            extracted = {}
            for key, config_entry in result_data.items():
                if isinstance(config_entry, dict) and "value" in config_entry:
                    extracted[key] = config_entry["value"]
                else:
                    extracted[key] = config_entry
            return extracted
        
        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        # Return an empty dictionary on failure
        return {}

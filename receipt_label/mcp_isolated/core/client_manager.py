"""Client manager for MCP server."""

import threading
from typing import Optional
from .config import load_pulumi_config

# Thread-safe client manager instance
_client_manager: Optional['ClientManager'] = None
_manager_lock = threading.Lock()


def get_client_manager():
    """Get or create the singleton client manager using the existing ClientManager."""
    global _client_manager

    if _client_manager is None:
        with _manager_lock:
            if _client_manager is None:  # Double-check pattern
                try:
                    # Load Pulumi config and set env vars
                    config = load_pulumi_config()

                    # Import and use the existing ClientManager
                    from receipt_label.utils.client_manager import (
                        ClientManager,
                        ClientConfig,
                    )

                    # Create config from environment (now populated by load_pulumi_config)
                    client_config = ClientConfig.from_env()
                    _client_manager = ClientManager(client_config)

                    # Store the loaded config for compatibility
                    _client_manager._pulumi_config = config

                except Exception as e:
                    raise RuntimeError(f"Failed to initialize ClientManager: {str(e)}")

    return _client_manager
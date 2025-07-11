"""Configuration module for receipt labeling system."""

from .local import LocalConfig, get_local_client_config

__all__ = ["LocalConfig", "get_local_client_config"]

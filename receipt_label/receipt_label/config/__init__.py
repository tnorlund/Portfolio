"""
Configuration module for receipt labeling system.

This module provides centralized configuration management for various
components of the receipt labeling system.
"""

from .decision_engine_config import DecisionEngineConfig, decision_engine_config

__all__ = [
    "DecisionEngineConfig",
    "decision_engine_config",
]
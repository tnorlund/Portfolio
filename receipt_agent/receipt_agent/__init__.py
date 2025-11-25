"""
Receipt Agent - Agentic validation for receipt metadata.

This package provides LangGraph-based agents for validating receipt metadata
using ChromaDB similarity search and cross-reference verification.
"""

from receipt_agent.agent.metadata_validator import MetadataValidatorAgent
from receipt_agent.clients.factory import (
    create_all_clients,
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)
from receipt_agent.graph.workflow import create_validation_graph
from receipt_agent.state.models import (
    ValidationResult,
    ValidationState,
    ValidationStatus,
    VerificationStep,
)

__version__ = "0.1.0"

__all__ = [
    # Main agent
    "MetadataValidatorAgent",
    # State models
    "ValidationResult",
    "ValidationState",
    "ValidationStatus",
    "VerificationStep",
    # Workflow
    "create_validation_graph",
    # Client factories
    "create_all_clients",
    "create_chroma_client",
    "create_dynamo_client",
    "create_embed_fn",
    "create_places_api",
]


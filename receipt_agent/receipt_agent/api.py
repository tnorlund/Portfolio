"""
Public façade for receipt_agent.

Provides stable, minimal imports for common usage:
- PlaceValidatorAgent (deterministic or agentic validation)
- Validation graph helpers
- Client factory helpers
"""

from receipt_agent.agents.agentic import (
    create_agentic_validation_graph,
    run_agentic_validation,
)
from receipt_agent.agents.place_validator import PlaceValidatorAgent
from receipt_agent.agents.validation import (
    create_validation_graph,
    run_validation,
)
from receipt_agent.clients.factory import (
    create_all_clients,
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)
from receipt_agent.state.models import (
    ValidationResult,
    ValidationState,
    ValidationStatus,
    VerificationStep,
)

__all__ = [
    "MetadataValidatorAgent",
    "create_validation_graph",
    "run_validation",
    "create_agentic_validation_graph",
    "run_agentic_validation",
    "create_all_clients",
    "create_chroma_client",
    "create_dynamo_client",
    "create_embed_fn",
    "create_places_api",
    "ValidationResult",
    "ValidationState",
    "ValidationStatus",
    "VerificationStep",
]

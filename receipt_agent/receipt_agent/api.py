"""
Public façade for receipt_agent.

Provides stable, minimal imports for common usage:
- Client factory helpers
- Shared result models
"""

from receipt_agent.clients.factory import (
    create_all_clients,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)
from receipt_agent.state.models import (
    ValidationResult,
    ValidationStatus,
    VerificationStep,
)

__all__ = [
    "create_all_clients",
    "create_dynamo_client",
    "create_embed_fn",
    "create_places_api",
    "ValidationResult",
    "ValidationStatus",
    "VerificationStep",
]

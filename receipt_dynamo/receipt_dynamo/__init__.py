"""DynamoDB utility package for receipt data."""

# mypy: ignore-errors

__version__ = "0.2.0"

# Additional exports that might not be in entities.__all__
# Import all entities
from receipt_dynamo.entities import *  # noqa: F401, F403
from receipt_dynamo.entities import (
    ContentPattern,
    ReceiptSection,
    SpatialPattern,
)

# Import services - requires boto3
try:
    from receipt_dynamo.services import *  # noqa: F401, F403
except ModuleNotFoundError:
    # Fallback placeholders when boto3 is not available
    class _ServicePlaceholder:  # type: ignore
        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for service classes")

    InstanceService = _ServicePlaceholder
    JobService = _ServicePlaceholder
    QueueService = _ServicePlaceholder

# Import DynamoDB clients - requires boto3
try:
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.data.resilient_dynamo_client import (
        ResilientDynamoClient,
    )
except ModuleNotFoundError:
    # Placeholders when boto3 is unavailable
    class DynamoClient:  # type: ignore
        """Placeholder for DynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for DynamoClient")

    class ResilientDynamoClient:  # type: ignore
        """Placeholder for ResilientDynamoClient when boto3 is unavailable."""

        def __init__(self, *_, **__):
            raise ModuleNotFoundError("boto3 is required for ResilientDynamoClient")


# Import data operations - requires boto3
try:
    from receipt_dynamo.data.export_image import export_image
except ModuleNotFoundError:

    def export_image(*_, **__):
        raise ModuleNotFoundError("boto3 is required for export_image")


try:
    from receipt_dynamo.data.import_image import import_image
except ModuleNotFoundError:

    def import_image(*_, **__):
        raise ModuleNotFoundError("boto3 is required for import_image")


# Build public API dynamically from submodules to eliminate code duplication
from receipt_dynamo import entities, services, utils

# Import resilience patterns
from receipt_dynamo.utils import *  # noqa: F401, F403

__all__ = [
    # Version
    "__version__",
    # DynamoDB clients
    "DynamoClient",
    "ResilientDynamoClient",
    # Data operations
    "export_image",
    "import_image",
]

# Add all entities and their converters (avoids code duplication)
__all__.extend(entities.__all__)

# Add all services
__all__.extend(services.__all__)

# Add all utilities
__all__.extend(utils.__all__)

"""Compaction handler - wrapper for complex implementation.

This temporarily wraps the existing implementation until we can properly refactor it.
"""

# For now, just import and use the existing implementation
# This is a temporary solution until we refactor the complex handlers
from ...unified_lambda.handlers.compaction import CompactionHandler

# Create a singleton instance
_handler = CompactionHandler()

# Export the handle function
handle = _handler.handle
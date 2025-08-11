"""Compaction handler - wrapper for complex implementation.

This temporarily wraps the existing implementation until we can properly refactor it.
"""

# For now, just import and use the existing implementation
# This is a temporary solution until we refactor the complex handlers
# In Lambda, unified_lambda is at the root level since we copy it there
import unified_lambda.handlers.compaction

# Create a singleton instance
_handler = unified_lambda.handlers.compaction.CompactionHandler()

# Export the handle function
handle = _handler.handle
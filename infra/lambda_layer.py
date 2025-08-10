"""
Compatibility shim for lambda_layer imports.

This module redirects all imports to fast_lambda_layer.py to prevent
duplicate layer creation. The old lambda_layer.py has been backed up
to lambda_layer.py.backup.

The fast_lambda_layer system is now the primary implementation and provides:
- Faster builds with async mode
- Better hash-based change detection  
- Simpler architecture without Step Functions
- Full backward compatibility through these exports
"""

# Import everything from fast_lambda_layer for backward compatibility
from fast_lambda_layer import (
    dynamo_layer,
    label_layer,
    upload_layer,
    fast_dynamo_layer,
    fast_label_layer,
    fast_upload_layer,
    FastLambdaLayer,
)

# Re-export for backward compatibility
__all__ = [
    "dynamo_layer",
    "label_layer", 
    "upload_layer",
    "fast_dynamo_layer",
    "fast_label_layer",
    "fast_upload_layer",
    "FastLambdaLayer",
]

# Note: The original lambda_layer.py was creating duplicate resources
# when both it and fast_lambda_layer.py were active. This shim ensures
# only one set of layers is created while maintaining compatibility
# with existing imports.
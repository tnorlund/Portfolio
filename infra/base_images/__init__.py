"""Base ECR images for receipt packages."""

# Export the most optimized version (v3) as the default
from .base_images_v3 import BaseImages

# For backward compatibility or testing, you can still import specific versions:
# from base_images.base_images import BaseImages as BaseImagesV1
# from base_images.base_images_v2 import BaseImages as BaseImagesV2
# from base_images.base_images_v3 import BaseImages as BaseImagesV3

__all__ = ["BaseImages"]
"""Public exception hierarchy for :mod:`receipt_logo`."""


class ReceiptLogoError(Exception):
    """Base class for failures produced by receipt-logo services."""


class LogoVectorizationError(ReceiptLogoError):
    """Base class for failures converting a raster logo to vectors."""


class LogoSourceError(LogoVectorizationError):
    """Raised when a source logo cannot be opened or decoded."""


class EmptyLogoError(LogoVectorizationError):
    """Raised when a source has no pixels eligible for vectorization."""


class PaletteExtractionError(LogoVectorizationError):
    """Raised when no visible color palette can be extracted."""


class InvalidAssetSlugError(ReceiptLogoError, ValueError):
    """Raised when an asset slug cannot produce a safe filename."""


class LogoAssetWriteError(ReceiptLogoError):
    """Raised when generated logo assets cannot be persisted."""


class UnknownLogoToolError(ReceiptLogoError):
    """Raised when the MCP service receives an unsupported tool name."""

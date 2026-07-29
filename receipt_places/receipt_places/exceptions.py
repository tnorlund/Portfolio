"""Public exception hierarchy for :mod:`receipt_places`."""


class ReceiptPlacesError(Exception):
    """Base class for failures produced by receipt_places."""


class PlacesConfigurationError(ValueError, ReceiptPlacesError):
    """Raised when a Places client cannot be configured."""


class PlacesResponseError(ReceiptPlacesError):
    """Base class for invalid or unsuccessful Places responses."""


class ParseError(PlacesResponseError):
    """Raised when a Places response cannot be parsed."""


class APIError(ParseError):
    """Raised when Google Places reports an unsuccessful response."""


class PlacesAPIError(APIError):
    """Backward-compatible API error carrying a legacy status value."""

    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


class PlaceAdaptationError(ValueError, PlacesResponseError):
    """Raised when a v1 Place lacks data required by the legacy model."""

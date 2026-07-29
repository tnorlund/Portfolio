"""Public exception hierarchy for :mod:`receipt_upload`."""


class ReceiptUploadError(Exception):
    """Base class for operational failures raised by this package."""


class OCRError(ReceiptUploadError):
    """Base class for Apple Vision OCR failures."""


class OCRInputError(OCRError, FileNotFoundError):
    """Raised when an OCR input file is missing or unusable."""


class OCRUnavailableError(OCRError):
    """Raised when the OCR runtime or platform is unavailable."""


class OCRScriptNotFoundError(OCRUnavailableError, FileNotFoundError):
    """Raised when the bundled Apple Vision OCR script is missing."""


class OCRPlatformError(OCRUnavailableError, ValueError):
    """Raised when Apple Vision OCR is requested on an unsupported platform."""


class OCRExecutionError(OCRError, ValueError):
    """Raised when the OCR process starts but exits unsuccessfully."""


class OCRResultError(OCRError, ValueError):
    """Raised when OCR output is missing or inconsistent."""


class OCROutputNotFoundError(OCRResultError, FileNotFoundError):
    """Raised when OCR does not produce an expected output file."""


class OCRStorageError(OCRError):
    """Raised when OCR input or output cannot be transferred to S3."""


class AVIFError(ReceiptUploadError):
    """Raised when AVIF encoding or upload fails."""

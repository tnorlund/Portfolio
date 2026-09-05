"""Errors shared by local transports."""


class ValidationError(ValueError):
    """The command cannot be applied as written."""


class Conflict(ValidationError):
    """Newer work exists; refresh before changing it."""

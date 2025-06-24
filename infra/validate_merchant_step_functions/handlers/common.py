"""Common utilities for Lambda handlers."""

from logging import INFO, Formatter, Logger, StreamHandler, getLogger


def setup_logger(name: str | None = None) -> Logger:
    """
    Set up a logger with consistent formatting for Lambda functions.

    Args:
        name: Optional logger name. If None, uses root logger.

    Returns:
        Configured logger instance
    """
    logger = getLogger(name)
    logger.setLevel(INFO)

    if len(logger.handlers) == 0:
        handler = StreamHandler()
        handler.setFormatter(
            Formatter(
                "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)

    return logger

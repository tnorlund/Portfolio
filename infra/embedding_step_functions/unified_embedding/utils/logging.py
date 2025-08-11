"""Centralized logging configuration for Lambda functions."""

import logging
import os
from typing import Optional


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a configured logger instance.
    
    Args:
        name: Logger name (defaults to calling module name)
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name or __name__)
    
    # Only configure if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler()
        
        # Use structured logging format for CloudWatch
        formatter = logging.Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        # Set level from environment or default to INFO
        level = os.environ.get("LOG_LEVEL", "INFO")
        logger.setLevel(getattr(logging, level, logging.INFO))
    
    return logger
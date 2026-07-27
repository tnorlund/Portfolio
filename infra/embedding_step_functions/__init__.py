"""Embedding Step Functions infrastructure.

Infrastructure is imported lazily so handler and ASL unit tests do not create
Pulumi resources merely by importing this package.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .infrastructure import EmbeddingInfrastructure

__all__ = ["EmbeddingInfrastructure"]


def __getattr__(name: str):
    if name == "EmbeddingInfrastructure":
        from .infrastructure import EmbeddingInfrastructure

        return EmbeddingInfrastructure
    raise AttributeError(name)

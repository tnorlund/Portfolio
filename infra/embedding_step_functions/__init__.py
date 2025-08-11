# Try to import the new hybrid infrastructure first, fall back to regular if not available
try:
    from .infrastructure_hybrid import HybridEmbeddingInfrastructure as EmbeddingInfrastructure
except ImportError:
    from .infrastructure import EmbeddingInfrastructure
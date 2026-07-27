"""Lazy exports for embedding infrastructure components."""

from importlib import import_module

_EXPORTS = {
    "CONTAINER_FUNCTION_NAMES": (
        "lambda_functions",
        "CONTAINER_FUNCTION_NAMES",
    ),
    "DockerImageComponent": ("docker_image", "DockerImageComponent"),
    "EmbedAllWorkflow": ("backfill_workflow", "EmbedAllWorkflow"),
    "LambdaFunctionsComponent": (
        "lambda_functions",
        "LambdaFunctionsComponent",
    ),
    "LineEmbeddingWorkflow": ("line_workflow", "LineEmbeddingWorkflow"),
    "MonitoringComponent": ("monitoring", "MonitoringComponent"),
    "WordEmbeddingWorkflow": ("word_workflow", "WordEmbeddingWorkflow"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, attribute)

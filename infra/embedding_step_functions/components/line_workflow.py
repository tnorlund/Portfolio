"""Line embedding workflow component."""

from typing import Optional

from pulumi import ResourceOptions

from .embedding_workflow import EmbeddingWorkflow


class LineEmbeddingWorkflow(EmbeddingWorkflow):
    """Line specialization of the shared embedding workflow."""

    def __init__(
        self,
        name: str,
        lambda_functions,
        batch_bucket=None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        del batch_bucket  # retained for call-site compatibility
        super().__init__(
            name,
            entity_type="lines",
            component_type="custom:embedding:LineWorkflow",
            lambda_functions=lambda_functions,
            opts=opts,
        )

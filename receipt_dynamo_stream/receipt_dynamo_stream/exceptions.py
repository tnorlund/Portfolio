"""Public exception hierarchy for :mod:`receipt_dynamo_stream`."""

from __future__ import annotations


class ReceiptDynamoStreamError(Exception):
    """Base class for DynamoDB stream processing failures."""


class QueuePublishError(ReceiptDynamoStreamError, RuntimeError):
    """Base class for failures while publishing stream messages."""

    def __init__(self, message: str, *, queue_name: str) -> None:
        self.queue_name = queue_name
        super().__init__(message)


class QueueConfigurationError(QueuePublishError):
    """A queue URL required for publishing is not configured."""

    def __init__(self, environment_variable: str, *, queue_name: str) -> None:
        self.environment_variable = environment_variable
        super().__init__(
            f"Queue URL for '{queue_name}' is not configured; "
            f"set {environment_variable}",
            queue_name=queue_name,
        )


class QueueServiceError(QueuePublishError):
    """SQS rejected or could not process a batch request."""

    def __init__(self, queue_name: str, batch_size: int) -> None:
        self.batch_size = batch_size
        super().__init__(
            f"Failed to send batch of {batch_size} message(s) to "
            f"'{queue_name}' queue",
            queue_name=queue_name,
        )


class QueueBatchFailureError(QueuePublishError):
    """SQS accepted a batch request but rejected one or more entries."""

    def __init__(
        self,
        queue_name: str,
        failed_entries: list[dict[str, object]],
    ) -> None:
        self.failed_entries = failed_entries
        super().__init__(
            f"SQS rejected {len(failed_entries)} message(s) for "
            f"'{queue_name}' queue",
            queue_name=queue_name,
        )


__all__ = [
    "ReceiptDynamoStreamError",
    "QueuePublishError",
    "QueueConfigurationError",
    "QueueServiceError",
    "QueueBatchFailureError",
]

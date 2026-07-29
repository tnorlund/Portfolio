"""Public exception hierarchy for :mod:`receipt_agent`."""


class ReceiptAgentError(Exception):
    """Base class for failures produced by receipt_agent."""


class ReceiptAgentConfigurationError(ValueError, ReceiptAgentError):
    """Raised when an agent service cannot be configured."""


class AgentExecutionError(ReceiptAgentError):
    """Raised when an agent workflow cannot complete its work."""


class LLMError(AgentExecutionError):
    """Base class for failures while interacting with an LLM."""


class LLMInvocationError(LLMError):
    """Raised when an LLM invocation fails or exhausts retries."""


class LLMRateLimitError(LLMError):
    """Raised when an LLM provider rejects work due to capacity limits."""

    def __init__(
        self,
        message: str,
        consecutive_errors: int = 0,
        total_errors: int = 0,
    ):
        super().__init__(message)
        self.consecutive_errors = consecutive_errors
        self.total_errors = total_errors


class EmptyResponseError(LLMInvocationError):
    """Raised when an LLM provider returns a response with no content."""

    def __init__(
        self,
        provider: str = "OpenRouter",
        message: str = "LLM returned empty response",
    ):
        super().__init__(f"{provider}: {message}")
        self.provider = provider

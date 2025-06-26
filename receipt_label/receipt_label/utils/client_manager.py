"""
Client management module with dependency injection support.

This module provides a cleaner way to manage external service clients
(DynamoDB, OpenAI, Pinecone) with proper dependency injection and testability.
"""

import os
from dataclasses import dataclass
from typing import Any, Optional

from openai import OpenAI
from pinecone import Pinecone
from receipt_dynamo import DynamoClient

from .ai_usage_tracker import AIUsageTracker


@dataclass
class ClientConfig:
    """Configuration for all external service clients."""

    dynamo_table: str
    openai_api_key: str
    pinecone_api_key: str
    pinecone_index_name: str
    pinecone_host: str
    track_usage: bool = True
    user_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ClientConfig":
        """Load configuration from environment variables."""
        return cls(
            dynamo_table=os.environ["DYNAMO_TABLE_NAME"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            pinecone_api_key=os.environ["PINECONE_API_KEY"],
            pinecone_index_name=os.environ["PINECONE_INDEX_NAME"],
            pinecone_host=os.environ["PINECONE_HOST"],
            track_usage=os.environ.get("TRACK_AI_USAGE", "true").lower() == "true",
            user_id=os.environ.get("USER_ID"),
        )


class ClientManager:
    """
    Manages client instances with lazy initialization and usage tracking.

    This class provides centralized access to all external service clients
    while supporting lazy initialization, usage tracking, and easy mocking for tests.
    """

    def __init__(self, config: ClientConfig):
        """
        Initialize the client manager with configuration.

        Args:
            config: Configuration object containing all necessary settings
        """
        self.config = config
        self._dynamo_client: Optional[DynamoClient] = None
        self._openai_client: Optional[OpenAI] = None
        self._pinecone_index: Optional[Any] = None  # Pinecone Index
        self._usage_tracker: Optional[AIUsageTracker] = None

    @property
    def dynamo(self) -> DynamoClient:
        """Get or create DynamoDB client."""
        if self._dynamo_client is None:
            self._dynamo_client = DynamoClient(self.config.dynamo_table)
        return self._dynamo_client

    @property
    def usage_tracker(self) -> Optional[AIUsageTracker]:
        """Get or create usage tracker."""
        if self.config.track_usage and self._usage_tracker is None:
            self._usage_tracker = AIUsageTracker(
                dynamo_client=self.dynamo,
                table_name=self.config.dynamo_table,
                user_id=self.config.user_id,
                track_to_dynamo=True,
                track_to_file=os.environ.get("TRACK_TO_FILE", "false").lower()
                == "true",
            )
        return self._usage_tracker

    @property
    def openai(self) -> OpenAI:
        """Get or create OpenAI client with optional usage tracking."""
        if self._openai_client is None:
            client = OpenAI(api_key=self.config.openai_api_key)

            # Wrap with usage tracking if enabled
            if self.config.track_usage and self.usage_tracker:
                client = AIUsageTracker.create_wrapped_openai_client(
                    client, self.usage_tracker
                )

            self._openai_client = client
        return self._openai_client

    @property
    def pinecone(self) -> Any:  # Returns Pinecone Index
        """Get or create Pinecone index."""
        if self._pinecone_index is None:
            pc = Pinecone(api_key=self.config.pinecone_api_key)
            self._pinecone_index = pc.Index(
                self.config.pinecone_index_name, host=self.config.pinecone_host
            )
        return self._pinecone_index

    def get_all_clients(self) -> tuple[DynamoClient, OpenAI, Any]:
        """
        Get all clients as a tuple (for backward compatibility).

        Returns:
            Tuple of (dynamo_client, openai_client, pinecone_index)
        """
        return self.dynamo, self.openai, self.pinecone

    def set_tracking_context(
        self,
        job_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        """Set context for usage tracking."""
        if self.usage_tracker:
            self.usage_tracker.set_context(
                job_id=job_id, batch_id=batch_id, user_id=user_id
            )

"""
Client management module with dependency injection support.

This module provides a cleaner way to manage external service clients
(DynamoDB, OpenAI, Pinecone) with proper dependency injection and testability.
"""

import os
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI
from pinecone import Pinecone
from pinecone.data.index import Index
from receipt_dynamo import DynamoClient


@dataclass
class ClientConfig:
    """Configuration for all external service clients."""

    dynamo_table: str
    openai_api_key: str
    pinecone_api_key: str
    pinecone_index_name: str
    pinecone_host: str

    @classmethod
    def from_env(cls) -> "ClientConfig":
        """Load configuration from environment variables."""
        return cls(
            dynamo_table=os.environ["DYNAMO_TABLE_NAME"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            pinecone_api_key=os.environ["PINECONE_API_KEY"],
            pinecone_index_name=os.environ["PINECONE_INDEX_NAME"],
            pinecone_host=os.environ["PINECONE_HOST"],
        )


class ClientManager:
    """
    Manages client instances with lazy initialization.

    This class provides centralized access to all external service clients
    while supporting lazy initialization and easy mocking for tests.
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
        self._pinecone_index: Optional[Index] = None

    @property
    def dynamo(self) -> DynamoClient:
        """Get or create DynamoDB client."""
        if self._dynamo_client is None:
            self._dynamo_client = DynamoClient(self.config.dynamo_table)
        return self._dynamo_client

    @property
    def openai(self) -> OpenAI:
        """Get or create OpenAI client."""
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=self.config.openai_api_key)
        return self._openai_client

    @property
    def pinecone(self) -> Index:
        """Get or create Pinecone index."""
        if self._pinecone_index is None:
            pc = Pinecone(api_key=self.config.pinecone_api_key)
            self._pinecone_index = pc.Index(
                self.config.pinecone_index_name, host=self.config.pinecone_host
            )
        return self._pinecone_index

    def get_all_clients(self) -> tuple[DynamoClient, OpenAI, Index]:
        """
        Get all clients as a tuple (for backward compatibility).

        Returns:
            Tuple of (dynamo_client, openai_client, pinecone_index)
        """
        return self.dynamo, self.openai, self.pinecone

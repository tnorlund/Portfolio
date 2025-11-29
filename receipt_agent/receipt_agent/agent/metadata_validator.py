"""
MetadataValidatorAgent - Main agent class for receipt metadata validation.

This module provides a high-level API for validating receipt metadata
using ChromaDB similarity search and cross-reference verification.

Supports two modes:
1. Deterministic (default): Fixed workflow with predetermined nodes
2. Agentic: LLM decides which tools to call with guardrailed tools
"""

import asyncio
import os
from typing import Any, Callable, Literal, Optional

import structlog
from langsmith import Client as LangSmithClient
from langsmith import traceable

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.graph.workflow import create_validation_graph, run_validation
from receipt_agent.graph.agentic_workflow import (
    create_agentic_validation_graph,
    run_agentic_validation,
)
from receipt_agent.state.models import ValidationResult, ValidationStatus
from receipt_agent.tools.registry import create_tool_registry

logger = structlog.get_logger(__name__)


class MetadataValidatorAgent:
    """
    Agent for validating receipt metadata using agentic search.

    This agent uses LangGraph to orchestrate a multi-step validation
    process that:
    1. Loads current metadata from DynamoDB
    2. Searches ChromaDB for similar receipts
    3. Verifies consistency across receipts
    4. Optionally checks Google Places
    5. Makes a final validation decision

    Example:
        ```python
        from receipt_agent import MetadataValidatorAgent
        from receipt_dynamo.data.dynamo_client import DynamoClient
        from receipt_chroma.data.chroma_client import ChromaClient

        # Initialize clients
        dynamo = DynamoClient(table_name="receipts")
        chroma = ChromaClient(persist_directory="/path/to/chroma")

        # Create agent
        agent = MetadataValidatorAgent(
            dynamo_client=dynamo,
            chroma_client=chroma,
        )

        # Validate a receipt
        result = await agent.validate(
            image_id="abc-123",
            receipt_id=1,
        )

        print(f"Status: {result.status}")
        print(f"Confidence: {result.confidence}")
        ```
    """

    def __init__(
        self,
        dynamo_client: Any,
        chroma_client: Any,
        embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
        places_api: Optional[Any] = None,
        settings: Optional[Settings] = None,
        enable_tracing: bool = True,
        mode: Literal["deterministic", "agentic"] = "deterministic",
    ):
        """
        Initialize the MetadataValidatorAgent.

        Args:
            dynamo_client: DynamoDB client (receipt_dynamo.data.dynamo_client.DynamoClient)
            chroma_client: ChromaDB client (receipt_chroma.data.chroma_client.ChromaClient)
            embed_fn: Function to generate embeddings. If None, uses OpenAI.
            places_api: Optional Google Places API client
            settings: Configuration settings
            enable_tracing: Whether to enable LangSmith tracing
            mode: Validation mode
                - "deterministic": Fixed workflow with predetermined steps (default)
                - "agentic": LLM autonomously decides which tools to call
        """
        self._settings = settings or get_settings()
        self._dynamo_client = dynamo_client
        self._chroma_client = chroma_client
        self._places_api = places_api
        self._enable_tracing = enable_tracing
        self._mode = mode

        # Initialize embedding function
        if embed_fn is not None:
            self._embed_fn = embed_fn
        else:
            self._embed_fn = self._create_default_embed_fn()

        # Create tool registry
        self._tool_registry = create_tool_registry(
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
            places_api=places_api,
            embed_fn=self._embed_fn,
        )

        # Initialize LangSmith if enabled
        self._langsmith_client: Optional[LangSmithClient] = None
        if enable_tracing:
            self._setup_langsmith()

        # Create the validation graph based on mode
        self._agentic_state_holder: Optional[dict] = None

        if mode == "agentic":
            self._graph, self._agentic_state_holder = create_agentic_validation_graph(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=self._embed_fn,
                places_api=places_api,
                settings=self._settings,
            )
        else:
            self._graph = create_validation_graph(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=self._embed_fn,
                places_api=places_api,
                settings=self._settings,
            )

        logger.info(
            "MetadataValidatorAgent initialized",
            mode=mode,
            ollama_model=self._settings.ollama_model,
            tracing_enabled=enable_tracing,
        )

    def _create_default_embed_fn(self) -> Callable[[list[str]], list[list[float]]]:
        """Create default OpenAI embedding function."""
        from openai import OpenAI

        api_key = self._settings.openai_api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "OpenAI API key required for embeddings. "
                "Set RECEIPT_AGENT_OPENAI_API_KEY or provide embed_fn."
            )

        client = OpenAI(api_key=api_key)
        model = self._settings.embedding_model

        def embed_fn(texts: list[str]) -> list[list[float]]:
            response = client.embeddings.create(
                input=texts,
                model=model,
            )
            return [d.embedding for d in response.data]

        return embed_fn

    def _setup_langsmith(self) -> None:
        """Configure LangSmith tracing."""
        api_key = self._settings.langsmith_api_key.get_secret_value()
        if not api_key:
            logger.warning("LangSmith API key not set - tracing disabled")
            self._enable_tracing = False
            return

        # Set environment variables for LangChain/LangGraph
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = api_key
        os.environ["LANGCHAIN_PROJECT"] = self._settings.langsmith_project

        try:
            self._langsmith_client = LangSmithClient(api_key=api_key)
            logger.info(
                "LangSmith tracing configured",
                project=self._settings.langsmith_project,
            )
        except Exception as e:
            logger.warning(f"Failed to initialize LangSmith: {e}")
            self._enable_tracing = False

    @traceable(name="validate_metadata")
    async def validate(
        self,
        image_id: str,
        receipt_id: int,
        thread_id: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate metadata for a single receipt.

        Args:
            image_id: UUID of the receipt image
            receipt_id: Receipt ID within the image
            thread_id: Optional thread ID for checkpointing

        Returns:
            ValidationResult with status, confidence, and reasoning
        """
        logger.info(
            "Starting metadata validation",
            image_id=image_id,
            receipt_id=receipt_id,
            mode=self._mode,
        )

        if self._mode == "agentic":
            return await self._validate_agentic(image_id, receipt_id)
        else:
            return await self._validate_deterministic(image_id, receipt_id, thread_id)

    async def _validate_deterministic(
        self,
        image_id: str,
        receipt_id: int,
        thread_id: Optional[str] = None,
    ) -> ValidationResult:
        """Run deterministic validation workflow."""
        try:
            final_state = await run_validation(
                image_id=image_id,
                receipt_id=receipt_id,
                dynamo_client=self._dynamo_client,
                chroma_client=self._chroma_client,
                embed_fn=self._embed_fn,
                places_api=self._places_api,
                settings=self._settings,
                thread_id=thread_id,
            )

            if final_state.result:
                logger.info(
                    "Validation complete",
                    image_id=image_id,
                    receipt_id=receipt_id,
                    status=final_state.result.status.value,
                    confidence=final_state.result.confidence,
                )
                return final_state.result

            # No result means something went wrong
            logger.warning(
                "Validation returned no result",
                image_id=image_id,
                receipt_id=receipt_id,
                errors=final_state.errors,
            )

            return ValidationResult(
                status=ValidationStatus.ERROR,
                confidence=0.0,
                reasoning=f"Validation failed: {', '.join(final_state.errors) or 'Unknown error'}",
            )

        except Exception as e:
            logger.error(
                "Validation error",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )
            return ValidationResult(
                status=ValidationStatus.ERROR,
                confidence=0.0,
                reasoning=f"Exception during validation: {str(e)}",
            )

    async def _validate_agentic(
        self,
        image_id: str,
        receipt_id: int,
    ) -> ValidationResult:
        """Run agentic validation with tool-calling LLM."""
        if self._agentic_state_holder is None:
            return ValidationResult(
                status=ValidationStatus.ERROR,
                confidence=0.0,
                reasoning="Agentic mode not properly initialized",
            )

        try:
            decision = await run_agentic_validation(
                graph=self._graph,
                state_holder=self._agentic_state_holder,
                image_id=image_id,
                receipt_id=receipt_id,
            )

            # Convert decision dict to ValidationResult
            status_map = {
                "VALIDATED": ValidationStatus.VALIDATED,
                "INVALID": ValidationStatus.INVALID,
                "NEEDS_REVIEW": ValidationStatus.NEEDS_REVIEW,
            }

            status = status_map.get(decision.get("status", "NEEDS_REVIEW"), ValidationStatus.NEEDS_REVIEW)

            logger.info(
                "Agentic validation complete",
                image_id=image_id,
                receipt_id=receipt_id,
                status=status.value,
                confidence=decision.get("confidence", 0.0),
            )

            return ValidationResult(
                status=status,
                confidence=decision.get("confidence", 0.0),
                reasoning=decision.get("reasoning", ""),
                recommendations=decision.get("evidence", []),
            )

        except Exception as e:
            logger.error(
                "Agentic validation error",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )
            return ValidationResult(
                status=ValidationStatus.ERROR,
                confidence=0.0,
                reasoning=f"Exception during agentic validation: {str(e)}",
            )

    def validate_sync(
        self,
        image_id: str,
        receipt_id: int,
        thread_id: Optional[str] = None,
    ) -> ValidationResult:
        """
        Synchronous wrapper for validate().

        Convenience method for non-async contexts.
        """
        return asyncio.run(
            self.validate(
                image_id=image_id,
                receipt_id=receipt_id,
                thread_id=thread_id,
            )
        )

    @traceable(name="validate_batch")
    async def validate_batch(
        self,
        receipts: list[tuple[str, int]],
        max_concurrency: int = 5,
    ) -> list[tuple[tuple[str, int], ValidationResult]]:
        """
        Validate metadata for multiple receipts concurrently.

        Args:
            receipts: List of (image_id, receipt_id) tuples
            max_concurrency: Maximum concurrent validations

        Returns:
            List of ((image_id, receipt_id), ValidationResult) tuples
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def validate_one(
            image_id: str, receipt_id: int
        ) -> tuple[tuple[str, int], ValidationResult]:
            async with semaphore:
                result = await self.validate(image_id, receipt_id)
                return ((image_id, receipt_id), result)

        tasks = [
            validate_one(image_id, receipt_id)
            for image_id, receipt_id in receipts
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions
        processed_results: list[tuple[tuple[str, int], ValidationResult]] = []
        for i, result in enumerate(results):
            key = receipts[i]
            if isinstance(result, Exception):
                processed_results.append((
                    key,
                    ValidationResult(
                        status=ValidationStatus.ERROR,
                        confidence=0.0,
                        reasoning=f"Exception: {str(result)}",
                    ),
                ))
            elif isinstance(result, tuple):
                processed_results.append(result)

        return processed_results

    def get_tools(self) -> list[Any]:
        """Get list of available tools for the agent."""
        return self._tool_registry.get_all_tools()

    def get_tool_descriptions(self) -> str:
        """Get formatted descriptions of available tools."""
        return self._tool_registry.get_tool_descriptions()

    @property
    def settings(self) -> Settings:
        """Get current settings."""
        return self._settings

    @property
    def mode(self) -> str:
        """Get current validation mode ('deterministic' or 'agentic')."""
        return self._mode


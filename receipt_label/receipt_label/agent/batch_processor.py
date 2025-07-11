"""
Batch processor for efficient GPT labeling.

This module handles batching of unlabeled words for cost-effective
processing through OpenAI's Batch API.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_label.constants import CORE_LABELS

logger = logging.getLogger(__name__)


@dataclass
class BatchRequest:
    """Represents a batch labeling request."""

    receipt_id: str
    words_by_line: Dict[int, List[Dict]]
    labeled_context: List[Dict]
    merchant_name: Optional[str]

    def to_prompt(self) -> str:
        """Convert to GPT prompt."""
        lines_text = []
        for line_id, words in sorted(self.words_by_line.items()):
            line_text = " ".join(w.get("text", "") for w in words)
            lines_text.append(f"Line {line_id}: {line_text}")

        prompt = f"""Label the following receipt words with appropriate labels.

Receipt from: {self.merchant_name or 'Unknown Merchant'}

Unlabeled text by line:
{chr(10).join(lines_text)}

Available labels:
{chr(10).join(f"- {label}: {desc}" for label, desc in CORE_LABELS.items())}

Context - Already labeled words:
{chr(10).join(f"'{ex['text']}' -> {ex['label']}" for ex in self.labeled_context[:10])}

For each word, provide:
1. The word text
2. The most appropriate label
3. Confidence score (0-1)
4. Brief reasoning

Return as JSON array."""

        return prompt


class BatchProcessor:
    """Handles batching and processing of unlabeled words through GPT."""

    # OpenAI token limits
    MAX_TOKENS_PER_BATCH = 50000  # Conservative limit for batch API
    APPROX_TOKENS_PER_WORD = 4  # Average tokens per word in prompt

    def __init__(self, client_manager):
        """
        Initialize batch processor.

        Args:
            client_manager: Client manager for accessing OpenAI
        """
        self.client_manager = client_manager
        self.pending_batches = []
        self.stats = {
            "total_batches": 0,
            "total_words": 0,
            "total_tokens": 0,
            "avg_batch_size": 0,
        }

    def create_batch(
        self,
        receipt_id: str,
        unlabeled_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_metadata: Optional[Dict] = None,
    ) -> List[BatchRequest]:
        """
        Create optimized batches for GPT processing.

        Args:
            receipt_id: Receipt identifier
            unlabeled_words: Words to label
            labeled_words: Already labeled words for context
            receipt_metadata: Optional metadata with merchant info

        Returns:
            List of batch requests
        """
        # Group words by line
        from receipt_label.agent.decision_engine import DecisionEngine

        engine = DecisionEngine()
        words_by_line = engine.group_words_by_line(unlabeled_words)

        # Extract labeled examples for context
        labeled_context = self._extract_labeled_context(labeled_words)

        # Get merchant name
        merchant_name = None
        if receipt_metadata:
            merchant_name = receipt_metadata.get(
                "merchant_name"
            ) or receipt_metadata.get("canonical_merchant_name")

        # Create batches respecting token limits
        batches = []
        current_batch_lines = {}
        current_tokens = 0

        for line_id, line_words in words_by_line.items():
            # Estimate tokens for this line
            line_tokens = len(line_words) * self.APPROX_TOKENS_PER_WORD

            # Check if adding this line would exceed limit
            if (
                current_tokens + line_tokens > self.MAX_TOKENS_PER_BATCH
                and current_batch_lines
            ):
                # Create batch with current lines
                batches.append(
                    BatchRequest(
                        receipt_id=receipt_id,
                        words_by_line=current_batch_lines,
                        labeled_context=labeled_context,
                        merchant_name=merchant_name,
                    )
                )
                current_batch_lines = {}
                current_tokens = 0

            # Add line to current batch
            current_batch_lines[line_id] = line_words
            current_tokens += line_tokens

        # Create final batch if needed
        if current_batch_lines:
            batches.append(
                BatchRequest(
                    receipt_id=receipt_id,
                    words_by_line=current_batch_lines,
                    labeled_context=labeled_context,
                    merchant_name=merchant_name,
                )
            )

        # Update stats
        self.stats["total_batches"] += len(batches)
        self.stats["total_words"] += len(unlabeled_words)

        logger.info(
            f"Created {len(batches)} batches for {len(unlabeled_words)} words"
        )

        return batches

    async def process_batch_async(
        self, batch: BatchRequest
    ) -> Dict[int, Dict]:
        """
        Process a batch through OpenAI API asynchronously.

        Args:
            batch: Batch request to process

        Returns:
            Dictionary mapping word_id to label info
        """
        prompt = batch.to_prompt()

        try:
            # Use OpenAI client from client manager
            response = await self.client_manager.openai.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a receipt labeling expert. Label each word with the most appropriate label from the provided list.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temperature for consistency
                max_tokens=4000,
            )

            # Parse response
            result = json.loads(response.choices[0].message.content)

            # Convert to word labels
            return self._parse_gpt_response(result, batch)

        except Exception as e:
            logger.error(f"Error processing batch: {e}")
            return {}

    def process_batch_sync(self, batch: BatchRequest) -> Dict[int, Dict]:
        """
        Process a batch through OpenAI API synchronously.

        Args:
            batch: Batch request to process

        Returns:
            Dictionary mapping word_id to label info
        """
        prompt = batch.to_prompt()

        try:
            # Use OpenAI client from client manager
            response = self.client_manager.openai.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a receipt labeling expert. Label each word with the most appropriate label from the provided list.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temperature for consistency
                max_tokens=4000,
            )

            # Parse response
            content = response.choices[0].message.content

            # Handle the response format
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON response: {content}")
                return {}

            # Convert to word labels
            return self._parse_gpt_response(result, batch)

        except Exception as e:
            logger.error(f"Error processing batch: {e}")
            return {}

    def prepare_batch_api_request(
        self, batches: List[BatchRequest]
    ) -> List[Dict]:
        """
        Prepare requests for OpenAI Batch API (24-hour processing).

        Args:
            batches: List of batch requests

        Returns:
            List of formatted requests for Batch API
        """
        requests = []

        for i, batch in enumerate(batches):
            request = {
                "custom_id": f"receipt_{batch.receipt_id}_batch_{i}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4-turbo-preview",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a receipt labeling expert. Label each word with the most appropriate label from the provided list.",
                        },
                        {"role": "user", "content": batch.to_prompt()},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "max_tokens": 4000,
                },
            }
            requests.append(request)

        return requests

    def _extract_labeled_context(
        self, labeled_words: Dict[int, Dict]
    ) -> List[Dict]:
        """Extract labeled examples for context."""
        context = []

        for word_id, label_info in labeled_words.items():
            context.append(
                {
                    "text": label_info.get("text", ""),
                    "label": label_info["label"],
                    "confidence": label_info.get("confidence", 0),
                    "source": label_info.get("source", "pattern"),
                }
            )

        # Sort by confidence
        context.sort(key=lambda x: x["confidence"], reverse=True)

        return context

    def _parse_gpt_response(
        self, response: Dict, batch: BatchRequest
    ) -> Dict[int, Dict]:
        """
        Parse GPT response into word labels.

        Args:
            response: GPT response (should contain 'labels' array)
            batch: Original batch request

        Returns:
            Dictionary mapping word_id to label info
        """
        word_labels = {}

        # Get the labels array from response
        labels = response.get("labels", [])
        if isinstance(response, list):
            labels = response

        # Create text to word mapping
        text_to_words = {}
        for line_words in batch.words_by_line.values():
            for word in line_words:
                text = word.get("text", "")
                if text not in text_to_words:
                    text_to_words[text] = []
                text_to_words[text].append(word)

        # Process each label
        for label_item in labels:
            if isinstance(label_item, dict):
                text = label_item.get("text", "").strip()
                label = label_item.get("label", "")
                confidence = float(label_item.get("confidence", 0.8))
                reasoning = label_item.get("reasoning", "")

                # Validate label
                if label not in CORE_LABELS:
                    logger.warning(
                        f"Invalid label '{label}' for text '{text}'"
                    )
                    continue

                # Find matching words
                if text in text_to_words:
                    for word in text_to_words[text]:
                        word_id = word.get("word_id")
                        if word_id is not None:
                            word_labels[word_id] = {
                                "label": label,
                                "confidence": confidence,
                                "source": "gpt",
                                "reasoning": reasoning,
                                "pattern_type": "gpt_batch",
                            }

        return word_labels

    def get_statistics(self) -> Dict:
        """Get batch processor statistics."""
        stats = self.stats.copy()

        if stats["total_batches"] > 0:
            stats["avg_batch_size"] = (
                stats["total_words"] / stats["total_batches"]
            )
            stats["avg_tokens_per_batch"] = (
                stats["total_tokens"] / stats["total_batches"]
            )

        return stats

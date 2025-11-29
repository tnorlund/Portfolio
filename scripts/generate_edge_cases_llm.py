#!/usr/bin/env python3
"""
Generate edge cases using LLM analysis with full context.

This script analyzes INVALID labels with full receipt context (metadata,
surrounding text, reasoning) and uses an LLM to identify patterns and
generate edge cases with explanations.

Usage:
    # Generate edge cases for MERCHANT_NAME
    python scripts/generate_edge_cases_llm.py --label-type MERCHANT_NAME

    # Use specific min occurrences
    python scripts/generate_edge_cases_llm.py --label-type MERCHANT_NAME --min-occurrences 5

    # Limit number of examples sent to LLM (for cost control)
    python scripts/generate_edge_cases_llm.py --label-type MERCHANT_NAME --max-examples 50
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available
    CORE_LABELS = {
        "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
        "STORE_HOURS": "Printed business hours or opening times for the merchant.",
        "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
        "WEBSITE": "Web or email address printed on the receipt.",
        "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
        "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
        "DATE": "Calendar date of the transaction.",
        "TIME": "Time of the transaction.",
        "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
        "COUPON": "Coupon code or description that reduces price.",
        "DISCOUNT": "Any non-coupon discount line item.",
        "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
        "QUANTITY": "Numeric count or weight of the item.",
        "UNIT_PRICE": "Price per single unit / weight before tax.",
        "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
        "SUBTOTAL": "Sum of all line totals before tax and discounts.",
        "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
        "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    }

try:
    from receipt_agent.config.settings import get_settings
    from langchain_ollama import ChatOllama
except ImportError:
    print("Error: receipt_agent package not found. Install dependencies first.")
    sys.exit(1)


class LLMEdgeCaseGenerator:
    """Generate edge cases using LLM analysis with full context."""

    def __init__(self, dynamo_client: DynamoClient, llm: Optional[Any] = None):
        self.dynamo = dynamo_client
        if llm is None:
            # Try to get API key from Pulumi secrets first
            from receipt_dynamo.data._pulumi import load_secrets

            pulumi_secrets = load_secrets("dev") or load_secrets("prod")
            api_key = None
            base_url = "https://ollama.com"
            model = "gpt-oss:120b-cloud"

            if pulumi_secrets:
                # Look for Ollama API key in Pulumi secrets (format: "portfolio:OLLAMA_API_KEY")
                api_key = (
                    pulumi_secrets.get("portfolio:OLLAMA_API_KEY") or
                    pulumi_secrets.get("OLLAMA_API_KEY") or
                    pulumi_secrets.get("receiptAgentOllamaApiKey") or
                    pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
                )
                base_url = (
                    pulumi_secrets.get("portfolio:OLLAMA_BASE_URL") or
                    pulumi_secrets.get("OLLAMA_BASE_URL") or
                    pulumi_secrets.get("receiptAgentOllamaBaseUrl") or
                    pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_BASE_URL") or
                    base_url
                )
                model = (
                    pulumi_secrets.get("portfolio:OLLAMA_MODEL") or
                    pulumi_secrets.get("OLLAMA_MODEL") or
                    pulumi_secrets.get("receiptAgentOllamaModel") or
                    pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_MODEL") or
                    model
                )

            # Fall back to settings if not in Pulumi
            if not api_key:
                settings = get_settings()
                api_key_str = settings.ollama_api_key
                if hasattr(api_key_str, 'get_secret_value'):
                    api_key = api_key_str.get_secret_value()
                else:
                    api_key = str(api_key_str) if api_key_str else None
                base_url = settings.ollama_base_url
                model = settings.ollama_model

            print(f"Using Ollama model: {model} at {base_url}")

            self.llm = ChatOllama(
                base_url=base_url,
                model=model,
                client_kwargs={
                    "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
                    "timeout": 120,
                },
                temperature=0.0,
            )
        else:
            self.llm = llm

    async def generate_edge_cases_with_llm(
        self,
        label_type: str,
        min_occurrences: int = 3,
        max_examples: int = 100,
    ) -> Dict[str, List[Dict]]:
        """
        Generate edge cases using LLM analysis.

        Args:
            label_type: CORE_LABEL to analyze
            min_occurrences: Minimum occurrences to consider
            max_examples: Maximum examples to send to LLM (for cost control)

        Returns:
            Dict mapping label_type -> list of edge cases
        """
        print(f"Phase 1: Collecting INVALID {label_type} labels...")
        invalid_labels = self._collect_invalid_labels(label_type)
        print(f"  Found {len(invalid_labels)} invalid labels")

        if not invalid_labels:
            return {}

        print(f"\nPhase 2: Enriching labels with full context...")
        enriched = await self._enrich_labels_with_context(invalid_labels[:max_examples])
        print(f"  Enriched {len(enriched)} labels (limited to {max_examples} for LLM)")

        print(f"\nPhase 3: Grouping by word text patterns...")
        grouped = self._group_by_pattern(enriched)

        print(f"\nPhase 4: Analyzing with LLM...")
        edge_cases = await self._analyze_with_llm(
            label_type, grouped, min_occurrences
        )

        return {label_type: edge_cases}

    def _collect_invalid_labels(self, label_type: str) -> List:
        """Query INVALID labels for the label type."""
        all_invalid = []
        last_key = None

        while True:
            batch, last_key = self.dynamo.list_receipt_word_labels_with_status(
                status=ValidationStatus.INVALID,
                limit=1000,
                last_evaluated_key=last_key,
            )

            # Filter by label_type
            batch = [l for l in batch if l.label == label_type.upper()]

            all_invalid.extend(batch)
            print(f"  Loaded {len(all_invalid)} invalid labels...", end="\r")

            if not last_key:
                break

        print()  # New line
        return all_invalid

    async def _enrich_labels_with_context(self, labels: List) -> List[Dict]:
        """Enrich labels with full context: metadata, line text, surrounding words, reasoning."""
        enriched = []

        for label in labels:
            try:
                # Get word text
                word = self.dynamo.get_receipt_word(
                    receipt_id=label.receipt_id,
                    image_id=label.image_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                )
                word_text = word.text if word else ""

                # Get receipt metadata
                metadata = self.dynamo.get_receipt_metadata(
                    label.image_id, label.receipt_id
                )
                merchant_name = metadata.merchant_name if metadata else None

                # Get line context
                line = self.dynamo.get_receipt_line(
                    receipt_id=label.receipt_id,
                    image_id=label.image_id,
                    line_id=label.line_id,
                )
                line_text = line.text if line else ""

                # Get surrounding words (3 before, 3 after)
                words_in_line = self.dynamo.list_receipt_words_from_line(
                    receipt_id=label.receipt_id,
                    image_id=label.image_id,
                    line_id=label.line_id,
                )
                surrounding_words = []
                if words_in_line:
                    words_in_line.sort(key=lambda w: w.word_id)
                    word_texts = [w.text for w in words_in_line]
                    try:
                        word_index = next(
                            i
                            for i, w in enumerate(words_in_line)
                            if w.word_id == label.word_id
                        )
                        start_idx = max(0, word_index - 3)
                        end_idx = min(len(word_texts), word_index + 4)
                        surrounding_words = word_texts[start_idx:end_idx]
                        # Mark target word
                        target_idx = word_index - start_idx
                        if surrounding_words:
                            surrounding_words[target_idx] = f"[{surrounding_words[target_idx]}]"
                    except StopIteration:
                        surrounding_words = word_texts

                # Get surrounding lines (2 before, 2 after)
                all_lines = self.dynamo.list_receipt_lines_from_receipt(
                    image_id=label.image_id, receipt_id=label.receipt_id
                )
                surrounding_lines = []
                if all_lines and line:
                    all_lines.sort(key=lambda l: l.line_id)
                    try:
                        line_index = next(
                            i
                            for i, l in enumerate(all_lines)
                            if l.line_id == label.line_id
                        )
                        start_idx = max(0, line_index - 2)
                        end_idx = min(len(all_lines), line_index + 3)
                        surrounding_lines = [
                            l.text for l in all_lines[start_idx:end_idx]
                        ]
                        # Mark target line
                        target_idx = line_index - start_idx
                        if surrounding_lines:
                            surrounding_lines[target_idx] = f">>> {surrounding_lines[target_idx]} <<<"
                    except StopIteration:
                        surrounding_lines = [l.text for l in all_lines[:5]]

                # Get ALL labels for THIS SPECIFIC WORD (audit trail)
                # This shows what labels were tried for this word and their validation status
                all_labels_for_word, _ = self.dynamo.list_receipt_word_labels_for_word(
                    image_id=label.image_id,
                    receipt_id=label.receipt_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                )

                # Build audit trail: what labels exist for this word and their statuses
                # Also trace consolidation chain to understand superseded labels
                word_label_audit_trail = []
                consolidation_chain = []

                # Sort all labels by timestamp to understand the sequence
                all_labels_sorted = sorted(
                    all_labels_for_word,
                    key=lambda l: l.timestamp_added if isinstance(l.timestamp_added, str) else str(l.timestamp_added)
                )

                # Build consolidation chain by tracing label_consolidated_from relationships
                # Simple approach: list all labels for this word, trace relationships via label_consolidated_from

                # Create a map of all labels by their label type for quick lookup
                label_map_by_type = {}
                for l in all_labels_sorted:
                    # A word can have multiple labels of the same type with different statuses
                    # So we need to track all of them
                    if l.label not in label_map_by_type:
                        label_map_by_type[l.label] = []
                    label_map_by_type[l.label].append(l)

                # Build consolidation chain by following label_consolidated_from relationships
                # Start with the current INVALID label and trace backwards/forwards

                # Find what this label superseded (if label_consolidated_from points to another label type)
                supersedes = None
                if label.label_consolidated_from and label.label_consolidated_from in label_map_by_type:
                    # This label was created from another label type - find the most recent one
                    superseded_labels = label_map_by_type[label.label_consolidated_from]
                    if superseded_labels:
                        # Get the most recent one (likely the one that was superseded)
                        superseded_label = max(superseded_labels, key=lambda l: l.timestamp_added)
                        supersedes = {
                            "label": superseded_label.label,
                            "validation_status": superseded_label.validation_status,
                            "reasoning": superseded_label.reasoning,
                            "timestamp_added": superseded_label.timestamp_added,
                        }
                        # Add to chain (oldest first)
                        consolidation_chain.append({
                            "label": superseded_label.label,
                            "status": superseded_label.validation_status,
                            "reasoning": superseded_label.reasoning,
                            "timestamp": superseded_label.timestamp_added,
                            "action": f"was superseded by {label.label}",
                        })

                # Add current label to chain
                consolidation_chain.append({
                    "label": label.label,
                    "status": label.validation_status,
                    "reasoning": label.reasoning,
                    "timestamp": label.timestamp_added,
                    "action": "current (INVALID)",
                })

                # Find what superseded this label (labels that have label_consolidated_from == this label's type)
                superseded_by = None
                for other_label in all_labels_sorted:
                    # If another label was created from this label type, it superseded this one
                    if (other_label.label_consolidated_from == label.label and
                        other_label.label != label.label):  # Different label type
                        superseded_by = {
                            "label": other_label.label,
                            "validation_status": other_label.validation_status,
                            "reasoning": other_label.reasoning,
                            "timestamp_added": other_label.timestamp_added,
                            "label_proposed_by": other_label.label_proposed_by,
                        }
                        # Add to chain
                        consolidation_chain.append({
                            "label": other_label.label,
                            "status": other_label.validation_status,
                            "reasoning": other_label.reasoning,
                            "timestamp": other_label.timestamp_added,
                            "action": f"superseded {label.label} (VALID)",
                        })
                        break

                # Also check for VALID labels of different types (cross-label confusion)
                # Even if there's no explicit consolidation chain, if there's a VALID label of a different type,
                # that's evidence of cross-label confusion
                if not superseded_by:
                    valid_labels = [
                        l for l in all_labels_sorted
                        if l.validation_status == ValidationStatus.VALID.value
                        and l.label != label.label
                    ]
                    if valid_labels:
                        # Take the most recent VALID label
                        latest_valid = max(valid_labels, key=lambda l: l.timestamp_added)
                        superseded_by = {
                            "label": latest_valid.label,
                            "validation_status": latest_valid.validation_status,
                            "reasoning": latest_valid.reasoning,
                            "timestamp_added": latest_valid.timestamp_added,
                            "label_proposed_by": latest_valid.label_proposed_by,
                            "note": "Different label type - cross-label confusion (no explicit consolidation chain)",
                        }

                # Build audit trail: ALL labels for this word (including the current one)
                # This shows the complete picture of all labels that exist for this word
                for other_label in all_labels_sorted:
                    # Include ALL labels, even the current one, to show the full picture
                    word_label_audit_trail.append({
                        "label": other_label.label,
                        "validation_status": other_label.validation_status,
                        "reasoning": other_label.reasoning[:200] if other_label.reasoning else None,
                        "label_consolidated_from": other_label.label_consolidated_from,
                        "timestamp_added": other_label.timestamp_added,
                        "label_proposed_by": other_label.label_proposed_by,
                        "is_current": other_label.label == label.label and other_label.validation_status == label.validation_status,
                    })

                # Get all labels for this receipt to see what other labels exist
                all_receipt_labels, _ = self.dynamo.list_receipt_word_labels_for_receipt(
                    image_id=label.image_id, receipt_id=label.receipt_id
                )
                # Count labels by type for context
                label_counts = {}
                labels_on_same_line = []
                for other_label in all_receipt_labels:
                    label_type = other_label.label
                    label_counts[label_type] = label_counts.get(label_type, 0) + 1
                    # Check if label is on the same line
                    if other_label.line_id == label.line_id and other_label.word_id != label.word_id:
                        other_word = self.dynamo.get_receipt_word(
                            receipt_id=other_label.receipt_id,
                            image_id=other_label.image_id,
                            line_id=other_label.line_id,
                            word_id=other_label.word_id,
                        )
                        if other_word:
                            labels_on_same_line.append({
                                "word": other_word.text,
                                "label": label_type,
                                "validation_status": other_label.validation_status,
                            })

                enriched.append(
                    {
                        "label": label,
                        "word_text": word_text,
                        "merchant_name": merchant_name,
                        "line_text": line_text,
                        "surrounding_words": surrounding_words,
                        "surrounding_lines": surrounding_lines,
                        "reasoning": label.reasoning,
                        "validation_status": label.validation_status,
                        "label_proposed_by": label.label_proposed_by,
                        "label_consolidated_from": label.label_consolidated_from,
                        "other_labels_on_receipt": label_counts,
                        "labels_on_same_line": labels_on_same_line,
                        "word_label_audit_trail": word_label_audit_trail,  # Shows what other labels exist for this word
                        "consolidation_chain": consolidation_chain,  # NEW: Full history of label changes
                        "superseded_by": superseded_by,  # NEW: What label replaced this one (if any)
                        "supersedes": supersedes,  # NEW: What label this one replaced (if any)
                    }
                )
            except Exception as e:
                print(f"    Warning: Could not enrich label {label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}: {e}")

        return enriched

    def _group_by_pattern(self, enriched_labels: List[Dict]) -> Dict[str, List[Dict]]:
        """Group enriched labels by normalized word text."""
        grouped = defaultdict(list)

        for item in enriched_labels:
            word_text = item["word_text"].strip() if item["word_text"] else ""
            if not word_text:
                continue

            normalized = word_text.upper()
            grouped[normalized].append(item)

        return dict(grouped)

    async def _analyze_with_llm(
        self,
        label_type: str,
        grouped_patterns: Dict[str, List[Dict]],
        min_occurrences: int,
    ) -> List[Dict]:
        """Use LLM to analyze patterns and generate edge cases."""
        from langchain_core.messages import HumanMessage
        from pydantic import BaseModel, Field

        class EdgeCaseCandidate(BaseModel):
            word_text: str = Field(description="The invalid word text (or pattern if regex)")
            normalized_word: str = Field(description="Normalized version for matching")
            merchant_name: Optional[str] = Field(
                None, description="Merchant name if merchant-specific, None if global"
            )
            match_type: str = Field(
                "exact",
                description="Match type: exact, prefix, suffix, contains, or regex",
            )
            pattern: Optional[str] = Field(
                None, description="Regex pattern if match_type is regex"
            )
            reason: str = Field(description="Why this is an invalid pattern for this label type")
            correct_label_type: Optional[str] = Field(
                None,
                description="What label type this word should actually be (if cross-label confusion detected). Use the consolidation chain and audit trail to determine this."
            )
            confidence: float = Field(
                description="Confidence score 0.0-1.0 that this is a valid edge case"
            )
            examples_count: int = Field(description="Number of examples analyzed", alias="example_count")
            can_resolve_needs_review: bool = Field(
                False,
                description="Can this edge case help automatically resolve NEEDS_REVIEW labels? True if this pattern is clear enough to automatically reject without human review."
            )
            suggested_rule: Optional[str] = Field(
                None,
                description="Suggested rule or prompt improvement that could prevent this pattern from being labeled incorrectly in the future"
            )

        class EdgeCaseAnalysis(BaseModel):
            edge_cases: List[EdgeCaseCandidate] = Field(
                description="List of edge case candidates"
            )
            summary: str = Field(description="Summary of analysis")

        # Filter to patterns with min_occurrences
        patterns_to_analyze = {
            k: v
            for k, v in grouped_patterns.items()
            if len(v) >= min_occurrences
        }

        if not patterns_to_analyze:
            print(f"  No patterns found with >= {min_occurrences} occurrences")
            print(f"  Pattern counts: {sorted([(k, len(v)) for k, v in grouped_patterns.items()], key=lambda x: x[1], reverse=True)[:10]}")
            return []

        print(f"  Analyzing {len(patterns_to_analyze)} patterns with LLM...")

        # Build prompt with examples
        prompt = f"""# Analyze Invalid {label_type} Labels and Generate Edge Cases

You are analyzing INVALID {label_type} labels to identify semantic patterns and cross-label confusion that should be rejected immediately without expensive LLM validation.

## Task

Review the following invalid label examples and identify:
1. **Semantic patterns**: What types of words/contexts are being incorrectly labeled as {label_type}?
2. **Cross-label confusion**: Are these words actually other label types (e.g., PRODUCT_NAME, ADDRESS_LINE, etc.)?
3. **Global vs merchant-specific**: Are patterns universal or merchant-specific?
4. **Pattern types**: Should this be exact match, prefix, suffix, contains, or regex?

## Label Type Definition

{CORE_LABELS.get(label_type, "N/A")}

## All CORE_LABELS (for context)

{chr(10).join(f"- **{k}**: {v}" for k, v in CORE_LABELS.items())}

## Invalid Label Examples

"""

        # Add examples for each pattern (limit to 3 examples per pattern)
        pattern_count = 0
        for normalized_word, examples in sorted(
            patterns_to_analyze.items(), key=lambda x: len(x[1]), reverse=True
        ):
            if pattern_count >= 20:  # Limit to top 20 patterns for cost control
                break

            pattern_count += 1
            sample_examples = examples[:3]  # 3 examples per pattern

            prompt += f"\n### Pattern: '{normalized_word}' ({len(examples)} occurrences)\n\n"

            for i, ex in enumerate(sample_examples, 1):
                prompt += f"**Example {i}:**\n"
                prompt += f"- Word: `{ex['word_text']}`\n"
                prompt += f"- Invalid Label: `{label_type}` (status: {ex['validation_status']})\n"
                merchant_name = ex.get('merchant_name')
                if merchant_name:
                    prompt += f"- **Actual Merchant Name (from Google Places - mostly accurate):** `{merchant_name}`\n"
                    # Check if the word is part of the merchant name
                    if ex['word_text'].upper() in merchant_name.upper():
                        prompt += f"  ⚠️  NOTE: The word `{ex['word_text']}` appears to be PART OF the merchant name `{merchant_name}` - this is likely a partial word issue!\n"
                    elif merchant_name.upper() in ex['word_text'].upper():
                        prompt += f"  ⚠️  NOTE: The merchant name `{merchant_name}` appears to be PART OF the word `{ex['word_text']}` - this might be a concatenation issue!\n"
                else:
                    prompt += f"- Merchant: Unknown (no metadata available)\n"
                prompt += f"- Line: `{ex['line_text']}`\n"

                # CONSOLIDATION CHAIN: Show the full history of label changes
                if ex.get('consolidation_chain'):
                    prompt += f"- **Label History (consolidation chain):**\n"
                    for idx, chain_item in enumerate(ex['consolidation_chain']):
                        status_emoji = "✅" if chain_item['status'] == 'VALID' else "❌" if chain_item['status'] == 'INVALID' else "⏳"
                        prompt += f"  {idx+1}. {status_emoji} `{chain_item['label']}` → {chain_item['status']} ({chain_item['action']})\n"
                        if chain_item.get('reasoning'):
                            prompt += f"     Reasoning: {chain_item['reasoning'][:150]}\n"
                        if chain_item.get('timestamp'):
                            prompt += f"     Timestamp: {chain_item['timestamp']}\n"
                    prompt += "\n"

                # SUPERSEDED BY: Show what label replaced this one (if any)
                if ex.get('superseded_by'):
                    prompt += f"- **Superseded By:** `{ex['superseded_by']['label']}` (VALID)\n"
                    prompt += f"  Reasoning: {ex['superseded_by']['reasoning'][:200] if ex['superseded_by'].get('reasoning') else 'N/A'}\n"
                    prompt += f"  This means `{label_type}` was replaced by `{ex['superseded_by']['label']}` - showing cross-label confusion!\n\n"

                # AUDIT TRAIL: Show ALL labels for this SAME word (always show, even if only one)
                audit_trail = ex.get('word_label_audit_trail', [])
                prompt += f"- **Audit Trail (ALL labels for this word - {len(audit_trail)} total):**\n"
                if audit_trail:
                    for audit in audit_trail:
                        status_emoji = "✅" if audit['validation_status'] == 'VALID' else "❌" if audit['validation_status'] == 'INVALID' else "⏳"
                        current_marker = " ← CURRENT" if audit.get('is_current') else ""
                        prompt += f"  {status_emoji} `{audit['label']}` → {audit['validation_status']}{current_marker}"
                        if audit.get('label_consolidated_from'):
                            prompt += f" (consolidated from: {audit['label_consolidated_from']})"
                        if audit.get('label_proposed_by'):
                            prompt += f" (proposed by: {audit['label_proposed_by']})"
                        prompt += "\n"
                        if audit.get('reasoning'):
                            prompt += f"    Reasoning: {audit['reasoning'][:150]}\n"
                        if audit.get('timestamp_added'):
                            prompt += f"    Timestamp: {audit['timestamp_added']}\n"
                else:
                    prompt += f"  (No other labels found for this word)\n"
                prompt += "\n"

                if ex['surrounding_words']:
                    prompt += f"- Surrounding words: `{' '.join(ex['surrounding_words'])}`\n"
                if ex['surrounding_lines']:
                    prompt += f"- Surrounding lines:\n"
                    for line in ex['surrounding_lines']:
                        prompt += f"  {line}\n"
                if ex.get('other_labels_on_receipt'):
                    prompt += f"- Other labels on receipt: {ex['other_labels_on_receipt']}\n"
                if ex.get('labels_on_same_line'):
                    same_line_str = ", ".join([f"{l['word']} ({l['label']})" for l in ex['labels_on_same_line'][:5]])
                    prompt += f"- Labels on same line: {same_line_str}\n"
                if ex['reasoning']:
                    prompt += f"- Original reasoning: {ex['reasoning'][:300]}\n"
                prompt += "\n"

        prompt += f"""
## Instructions

1. **Use the Consolidation Chain**: This is the MOST IMPORTANT context! The "Label History" shows the full sequence of label changes:
   - If you see: `MERCHANT_NAME (INVALID) → PRODUCT_NAME (VALID)`, this means MERCHANT_NAME was superseded by PRODUCT_NAME
   - The reasoning in each step explains WHY the label changed
   - This shows cross-label confusion patterns clearly (e.g., "MERCHANT_NAME might be mislabeled as PRODUCT_NAME when buying store-brand products")

2. **Use the Superseded By field**: If an INVALID label has a "Superseded By" section, it means another label replaced it:
   - This is direct evidence of cross-label confusion
   - The reasoning explains why the replacement happened
   - Use this to identify patterns like "store-brand products labeled as merchant names"

3. **Use the Audit Trail**: The "Audit Trail" shows what OTHER labels exist for the SAME word:
   - If a word has `{label_type}=INVALID` but `PRODUCT_NAME=VALID`, it means the word was incorrectly labeled as {label_type} but is actually a product name
   - If a word has `{label_type}=INVALID` but `ADDRESS_LINE=VALID`, it means the word was incorrectly labeled as {label_type} but is actually part of an address
   - Check `label_consolidated_from` to see if labels were replaced

2. **Use the Actual Merchant Name**: The "Actual Merchant Name" field comes from Google Places and is mostly accurate:
   - If the word is PART OF the merchant name (e.g., word="Decor", merchant="Floor & Decor"), this is a partial word issue - the word should NOT be labeled as MERCHANT_NAME because it's only a fragment
   - If the word MATCHES the merchant name exactly, but is still INVALID, investigate why (e.g., OCR error, wrong context)
   - If the word is DIFFERENT from the merchant name, it's likely a different label type (e.g., PRODUCT_NAME, ADDRESS_LINE)

3. **Identify semantic patterns**: Use the consolidation chain, audit trail, and merchant name to identify patterns:
   - **Partial merchant names**: If the word is part of the actual merchant name (from Google Places), this is a partial word issue - reject it as MERCHANT_NAME
   - **Store-brand products**: If consolidation chain shows `{label_type} → PRODUCT_NAME`, this is store-brand confusion
   - **Location names**: If consolidation chain shows `{label_type} → ADDRESS_LINE`, this is address confusion
   - **Common words**: Articles/conjunctions (e.g., "The", "AND") that shouldn't be {label_type}
   - **Cross-label confusion**: Use consolidation chain to confirm - if `{label_type}` was replaced by another label type, that's the pattern!

2. **Classify scope**: Is it global (all merchants) or merchant-specific?

3. **Determine match type**:
   - `exact`: Exact word match
   - `prefix`: Word starts with this pattern
   - `suffix`: Word ends with this pattern
   - `contains`: Word contains this pattern
   - `regex`: Use regex pattern (provide pattern)

4. **Write clear reason**: Explain:
   - Why this is invalid for {label_type}
   - What label type it might actually be (if applicable) - use the consolidation chain and audit trail to determine this
   - The semantic pattern you identified

5. **Identify correct label type**: If the consolidation chain or audit trail shows cross-label confusion:
   - What label type should this word actually be?
   - For example: If MERCHANT_NAME (INVALID) → PRODUCT_NAME (VALID), the correct_label_type should be PRODUCT_NAME
   - This helps define rules to automatically resolve NEEDS_REVIEW labels

6. **Can resolve NEEDS_REVIEW**: Determine if this pattern is clear enough to automatically resolve NEEDS_REVIEW labels:
   - If the pattern is unambiguous (e.g., punctuation-only, common words, clear cross-label confusion), set `can_resolve_needs_review=True`
   - If the pattern requires context or might have edge cases, set `can_resolve_needs_review=False`

7. **Suggest rule improvement**: If this pattern could be prevented with better prompts or rules:
   - Suggest a rule or prompt improvement
   - For example: "Reject words ending with ':' as they are typically prefixes, not merchant names"
   - This helps improve the labeling system to prevent future errors

8. **Assign confidence**: How confident are you this is a valid edge case? (0.0-1.0)

## Criteria for Edge Cases

✅ **Good edge cases:**
- Punctuation-only words ("-", ".", ":")
- Common English words that are clearly not {label_type} ("The", "AND", "Store")
- Location names from addresses (e.g., "Westlake", "Village", "Oaks")
- Store-brand product names (e.g., "Kirkland" from Costco products)
- Partial words from merchant names (e.g., "Decor" from "Floor & Decor")
- Words that appear in wrong context (e.g., product names labeled as merchant names)
- OCR errors that are consistent

❌ **Bad edge cases:**
- Words that might be valid in some contexts
- Patterns with low occurrence counts
- Merchant-specific quirks that are actually valid
- Edge cases that would block legitimate labels

## Output

Provide a JSON response with:
- `edge_cases`: List of edge case candidates with:
  - Pattern details (word_text, normalized_word, match_type, pattern)
  - Why it's invalid (reason)
  - What it should be (correct_label_type) - use consolidation chain/audit trail to determine
  - Whether it can help resolve NEEDS_REVIEW labels (can_resolve_needs_review)
  - Suggested rule improvement (suggested_rule)
  - Confidence and examples count
- `summary`: A plain text string (NOT a structured object) that summarizes:
  - Common cross-label confusion patterns (e.g., "MERCHANT_NAME is often confused with PRODUCT_NAME for store-brand products")
  - Patterns that could help automatically resolve NEEDS_REVIEW labels
  - Suggested rule improvements for the labeling system
  - Keep it concise (2-4 sentences)

**IMPORTANT**: The `summary` field must be a plain text string, not a JSON object or dictionary.

**Goal**: Identify patterns that can help define rules to automatically resolve NEEDS_REVIEW labels. Focus on:
1. Clear, unambiguous patterns that can be automatically rejected
2. Cross-label confusion patterns where we can suggest the correct label type
3. Rule improvements that could prevent these patterns in the future

Focus on high-confidence, clearly invalid patterns. Be conservative - it's better to miss an edge case than to create a false positive that blocks valid labels.
"""

        try:
            from langchain_core.runnables import RunnableConfig

            messages = [HumanMessage(content=prompt)]
            config = RunnableConfig(
                run_name="analyze_edge_cases",
                metadata={
                    "task": "edge_case_analysis",
                    "label_type": label_type,
                    "patterns_analyzed": len(patterns_to_analyze),
                },
                tags=["edge-case-generator", "llm-analysis"],
            )

            llm_with_schema = ChatOllama(
                model=self.llm.model,
                base_url=self.llm.base_url,
                client_kwargs=self.llm.client_kwargs,
                format=EdgeCaseAnalysis.model_json_schema(),
                temperature=0.0,
            )
            llm_structured = llm_with_schema.with_structured_output(EdgeCaseAnalysis)

            print("  Calling LLM for analysis...")

            # Save prompt for review
            prompt_file = f"prompt_{label_type.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(prompt)
            print(f"  Prompt saved to {prompt_file} for review")

            response = await llm_structured.ainvoke(messages, config=config)

            edge_cases = []
            # Extract edge_cases from response (handles both Pydantic model and dict)
            response_edge_cases = []
            if isinstance(response, EdgeCaseAnalysis):
                response_edge_cases = response.edge_cases
            elif hasattr(response, 'edge_cases'):
                response_edge_cases = response.edge_cases  # type: ignore
            elif isinstance(response, dict):
                response_edge_cases = response.get('edge_cases', [])

            for candidate in response_edge_cases:
                # Only include high-confidence edge cases
                if candidate.confidence >= 0.7:
                    edge_cases.append(
                        {
                            "word_text": candidate.word_text,
                            "normalized_word": candidate.normalized_word,
                            "merchant_name": candidate.merchant_name,
                            "match_type": candidate.match_type,
                            "pattern": candidate.pattern,
                            "reason": candidate.reason,
                            "correct_label_type": candidate.correct_label_type,
                            "can_resolve_needs_review": candidate.can_resolve_needs_review,
                            "suggested_rule": candidate.suggested_rule,
                            "confidence": candidate.confidence,
                            "count": candidate.examples_count,
                        }
                    )

            print(f"  LLM identified {len(edge_cases)} high-confidence edge cases")
            summary = ""
            if isinstance(response, EdgeCaseAnalysis):
                summary = response.summary
            elif hasattr(response, 'summary'):
                summary = response.summary  # type: ignore
            elif isinstance(response, dict):
                summary = response.get('summary', '')
            if summary:
                print(f"  Summary: {summary[:200]}...")

            return edge_cases

        except Exception as e:
            print(f"  Error in LLM analysis: {e}")
            import traceback

            traceback.print_exc()
            return []


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate edge cases using LLM analysis with full context"
    )
    parser.add_argument(
        "--label-type",
        type=str,
        required=True,
        help="CORE_LABEL to analyze (e.g., MERCHANT_NAME)",
    )
    parser.add_argument(
        "--min-occurrences",
        type=int,
        default=3,
        help="Minimum occurrences to consider (default: 3)",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=100,
        help="Maximum examples to send to LLM (default: 100, for cost control)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="edge_cases_llm.json",
        help="Output JSON file path (default: edge_cases_llm.json)",
    )

    args = parser.parse_args()

    # Initialize clients
    try:
        from receipt_dynamo.data._pulumi import load_env

        pulumi_outputs = load_env("dev") or load_env("prod")

        if pulumi_outputs and "dynamodb_table_name" in pulumi_outputs:
            table_name = pulumi_outputs["dynamodb_table_name"]
            if "dynamodb_table_arn" in pulumi_outputs:
                arn = pulumi_outputs["dynamodb_table_arn"]
                region = arn.split(":")[3] if ":" in arn else pulumi_outputs.get("region", "us-east-1")
            else:
                region = pulumi_outputs.get("region", "us-east-1")
        else:
            settings = get_settings()
            table_name = settings.dynamo_table_name
            region = settings.aws_region

        dynamo = DynamoClient(table_name=table_name, region=region)
    except Exception as e:
        print(f"Error initializing DynamoDB client: {e}")
        sys.exit(1)

    # Generate edge cases
    generator = LLMEdgeCaseGenerator(dynamo)

    print("=" * 60)
    print("LLM Edge Case Generator")
    print("=" * 60)
    print(f"Label Type: {args.label_type}")
    print(f"Min Occurrences: {args.min_occurrences}")
    print(f"Max Examples: {args.max_examples}")
    print(f"Output: {args.output}")
    print("=" * 60)
    print()

    try:
        edge_cases = await generator.generate_edge_cases_with_llm(
            label_type=args.label_type,
            min_occurrences=args.min_occurrences,
            max_examples=args.max_examples,
        )

        # Save results
        output_data = {
            "generated_at": datetime.now().isoformat(),
            "label_type": args.label_type,
            "min_occurrences": args.min_occurrences,
            "max_examples": args.max_examples,
            "edge_cases": edge_cases.get(args.label_type, []),
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"\nResults saved to {args.output}")
        print(f"Generated {len(edge_cases.get(args.label_type, []))} edge cases")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())


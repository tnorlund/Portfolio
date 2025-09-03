#!/usr/bin/env python3
"""
Label updater for applying PydanticOutputParser results to receipt words in DynamoDB.

This module handles the process of:
1. Taking CurrencyLabel results from LLM analysis
2. Finding matching words in the receipt using receipt_word data
3. Checking existing labels using receipt_word_label data  
4. Adding/updating labels as needed with conflict detection
"""

import logging
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
from dataclasses import dataclass

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus

from receipt_label.receipt_models import CurrencyLabel, LabelType

logger = logging.getLogger(__name__)


@dataclass
class WordMatchResult:
    """Result of matching a currency label to receipt words."""
    
    word: ReceiptWord
    match_confidence: float  # How well this word matches the currency text
    exact_match: bool        # Whether text matches exactly


@dataclass
class LabelUpdateResult:
    """Result of attempting to update a word's labels."""
    
    word: ReceiptWord
    currency_label: CurrencyLabel
    action_taken: str        # "added", "updated", "skipped", "conflict"
    existing_labels: List[ReceiptWordLabel]
    new_label: Optional[ReceiptWordLabel] = None
    conflict_reason: Optional[str] = None


class ReceiptLabelUpdater:
    """Updates receipt word labels based on LLM currency classification results."""
    
    def __init__(self, client: DynamoClient, merchant_name: Optional[str] = None):
        self.client = client
        self.merchant_name = merchant_name or "unknown"
        
    async def apply_currency_labels(
        self, 
        image_id: str,
        receipt_id: int,
        currency_labels: List[CurrencyLabel],
        dry_run: bool = False,
        preload_existing_labels: bool = True
    ) -> List[LabelUpdateResult]:
        """Apply currency labels to receipt words in DynamoDB.
        
        Args:
            image_id: The receipt image UUID
            receipt_id: The receipt ID number
            currency_labels: List of currency labels from LLM analysis
            dry_run: If True, show what would be done without making changes
            preload_existing_labels: If True, preload all existing labels to avoid redundant queries
            
        Returns:
            List of update results showing what was done for each label
        """
        results = []
        existing_labels_cache = None
        
        logger.info(f"Applying {len(currency_labels)} currency labels to {image_id}/{receipt_id}")
        
        # Optionally preload all existing labels to avoid redundant queries
        if preload_existing_labels and currency_labels:
            logger.info(f"Preloading existing labels for optimization...")
            existing_labels_cache = {}
            
            # Preload ALL labels for this receipt (more efficient than individual line queries)
            all_labels, _ = self.client.list_receipt_word_labels_for_receipt(
                image_id=image_id,
                receipt_id=receipt_id
            )
            
            # Group labels by (line_id, word_id) for fast lookup
            for label in all_labels:
                word_key = (label.line_id, label.word_id)
                if word_key not in existing_labels_cache:
                    existing_labels_cache[word_key] = []
                existing_labels_cache[word_key].append(label)
        
        for currency_label in currency_labels:
            try:
                result = await self._apply_single_currency_label(
                    image_id, receipt_id, currency_label, dry_run, existing_labels_cache
                )
                results.append(result)
                
            except Exception as e:
                logger.error(f"Error applying label {currency_label.label_type.value} for ${currency_label.value:.2f}: {e}")
                results.append(LabelUpdateResult(
                    word=None,
                    currency_label=currency_label,
                    action_taken="error",
                    existing_labels=[],
                    conflict_reason=str(e)
                ))
        
        return results
    
    async def _apply_single_currency_label(
        self,
        image_id: str,
        receipt_id: int, 
        currency_label: CurrencyLabel,
        dry_run: bool,
        existing_labels_cache: Optional[Dict[Tuple[int, int], List[ReceiptWordLabel]]] = None
    ) -> LabelUpdateResult:
        """Apply a single currency label to the best matching word."""
        
        # Step 1: Find candidate words from the line_ids
        candidate_words = await self._get_words_from_line_ids(
            image_id, receipt_id, currency_label.line_ids
        )
        
        if not candidate_words:
            logger.warning(f"No words found for line_ids {currency_label.line_ids}")
            return LabelUpdateResult(
                word=None,
                currency_label=currency_label,
                action_taken="no_words_found",
                existing_labels=[],
                conflict_reason="No words found for specified line_ids"
            )
        
        # Step 2: Match currency text to specific word
        best_match = self._find_best_word_match(candidate_words, currency_label)
        
        if not best_match:
            logger.warning(f"No word match found for '{currency_label.word_text}' in line_ids {currency_label.line_ids}")
            return LabelUpdateResult(
                word=None,
                currency_label=currency_label,
                action_taken="no_match_found",
                existing_labels=[],
                conflict_reason=f"No word matching '{currency_label.word_text}' found"
            )
        
        # Step 3: Check existing labels for this word
        word_key = (best_match.word.line_id, best_match.word.word_id)
        if existing_labels_cache is not None:
            # Use cache (empty list if no labels for this word)
            existing_labels = existing_labels_cache.get(word_key, [])
        else:
            # Fallback to individual query (should only happen if preload_existing_labels=False)
            existing_labels, _ = self.client.list_receipt_word_labels_for_word(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=best_match.word.line_id,
                word_id=best_match.word.word_id
            )
        
        # Step 4: Decide what action to take
        action = self._determine_label_action(
            existing_labels, currency_label.label_type.value
        )
        
        if action == "skip":
            return LabelUpdateResult(
                word=best_match.word,
                currency_label=currency_label,
                action_taken="skipped", 
                existing_labels=existing_labels,
                conflict_reason="Label already exists with same type"
            )
        
        # Step 5: Create and apply new label (if not dry run)
        new_label = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=best_match.word.line_id,
            word_id=best_match.word.word_id,
            label=currency_label.label_type.value,
            reasoning=f"LLM Classification: {currency_label.reasoning} (confidence: {currency_label.confidence:.2f})",
            timestamp_added=datetime.now(),
            validation_status=ValidationStatus.VALID.value,
            label_proposed_by=f"{self.merchant_name.lower()}_analyzer_llm"
        )
        
        if not dry_run:
            if action == "add":
                self.client.add_receipt_word_label(new_label)
                logger.info(f"Added {currency_label.label_type.value} label to word '{best_match.word.text}' (${currency_label.value:.2f})")
            elif action == "update":
                # Find the currently VALID conflicting label
                valid_conflicting_labels = [
                    label for label in existing_labels 
                    if label.validation_status == ValidationStatus.VALID.value 
                    and label.label != currency_label.label_type.value
                ]
                
                if valid_conflicting_labels:
                    # Should only be one VALID conflicting label
                    conflicting_label = valid_conflicting_labels[0]
                    
                    # Step 1: Mark the conflicting label as INVALID
                    conflicting_label.validation_status = ValidationStatus.INVALID.value
                    conflicting_label.reasoning = f"Superseded by {currency_label.label_type.value} from LLM analysis. Original reasoning: {conflicting_label.reasoning}"
                    self.client.update_receipt_word_label(conflicting_label)
                    
                    # Step 2: Add new VALID consolidated label
                    consolidated_label = ReceiptWordLabel(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=best_match.word.line_id,
                        word_id=best_match.word.word_id,
                        label=currency_label.label_type.value,  # New correct label
                        reasoning=f"LLM Consolidation: {currency_label.reasoning} (confidence: {currency_label.confidence:.2f}). Supersedes previous '{conflicting_label.label}' label.",
                        timestamp_added=datetime.now(),
                        validation_status=ValidationStatus.VALID.value,
                        label_proposed_by=f"{self.merchant_name.lower()}_analyzer_llm",
                        label_consolidated_from=conflicting_label.label  # Preserve history!
                    )
                    
                    self.client.add_receipt_word_label(consolidated_label)
                    logger.info(f"Updated label for word '{best_match.word.text}': {conflicting_label.label} marked INVALID, {currency_label.label_type.value} added as VALID")
        
        return LabelUpdateResult(
            word=best_match.word,
            currency_label=currency_label,
            action_taken=action if not dry_run else f"would_{action}",
            existing_labels=existing_labels,
            new_label=new_label
        )
    
    async def _get_words_from_line_ids(
        self, 
        image_id: str, 
        receipt_id: int, 
        line_ids: List[int]
    ) -> List[ReceiptWord]:
        """Get all receipt words from the specified line IDs."""
        
        all_words = []
        for line_id in line_ids:
            words = self.client.list_receipt_words_from_line(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id
            )
            all_words.extend(words)
        
        return all_words
    
    def _find_best_word_match(
        self, 
        candidate_words: List[ReceiptWord], 
        currency_label: CurrencyLabel
    ) -> Optional[WordMatchResult]:
        """Find the word that best matches the currency label text."""
        
        target_text = currency_label.word_text.strip()
        
        # Remove common currency symbols and normalize
        normalized_target = target_text.replace("$", "").replace(",", "").strip()
        
        best_match = None
        highest_confidence = 0.0
        
        for word in candidate_words:
            word_text = word.text.strip()
            normalized_word = word_text.replace("$", "").replace(",", "").strip()
            
            # Exact match (highest priority)
            if word_text == target_text or normalized_word == normalized_target:
                return WordMatchResult(
                    word=word,
                    match_confidence=1.0,
                    exact_match=True
                )
            
            # Partial matches for currency values
            if normalized_target in normalized_word or normalized_word in normalized_target:
                # Check if it's a reasonable currency match
                try:
                    word_value = float(normalized_word)
                    target_value = float(normalized_target)
                    if abs(word_value - target_value) < 0.01:  # Values match within 1 cent
                        confidence = 0.9
                        if confidence > highest_confidence:
                            highest_confidence = confidence
                            best_match = WordMatchResult(
                                word=word,
                                match_confidence=confidence,
                                exact_match=False
                            )
                except ValueError:
                    continue
            
            # Fuzzy matching for currency text
            if len(normalized_target) > 2:  # Only for meaningful strings
                # Simple similarity check
                if normalized_target.startswith(normalized_word) or normalized_word.startswith(normalized_target):
                    confidence = min(len(normalized_word), len(normalized_target)) / max(len(normalized_word), len(normalized_target))
                    if confidence > 0.7 and confidence > highest_confidence:
                        highest_confidence = confidence
                        best_match = WordMatchResult(
                            word=word,
                            match_confidence=confidence,
                            exact_match=False
                        )
        
        return best_match
    
    def _determine_label_action(
        self, 
        existing_labels: List[ReceiptWordLabel], 
        new_label_type: str
    ) -> str:
        """Determine what action to take based on existing labels.
        
        Returns:
            "add": Add the new label
            "skip": Skip because same label already exists
            "update": Update/replace conflicting label  
        """
        
        if not existing_labels:
            return "add"
        
        # Find currently VALID labels (not historical ones)
        valid_labels = [label for label in existing_labels if label.validation_status == ValidationStatus.VALID.value]
        valid_types = {label.label for label in valid_labels}
        
        # Check if exact same VALID label type already exists
        if new_label_type in valid_types:
            return "skip"  # Same VALID label already exists
        
        # Define mutually exclusive label groups
        currency_types = {LabelType.GRAND_TOTAL.value, LabelType.TAX.value, LabelType.LINE_TOTAL.value, LabelType.SUBTOTAL.value, LabelType.UNIT_PRICE.value}
        line_item_types = {LabelType.PRODUCT_NAME.value, LabelType.QUANTITY.value}
        
        # Check for currency label conflicts (mutually exclusive)
        if new_label_type in currency_types:
            existing_currency_types = valid_types & currency_types
            if existing_currency_types:
                # Special case: LINE_TOTAL takes precedence over UNIT_PRICE
                if LabelType.LINE_TOTAL.value in existing_currency_types and new_label_type == LabelType.UNIT_PRICE.value:
                    logger.info(f"Skipping UNIT_PRICE - word already has VALID LINE_TOTAL label (avoiding conflict)")
                    return "skip"  # LINE_TOTAL takes precedence
                
                logger.warning(f"Currency label conflict: existing VALID {existing_currency_types}, new {new_label_type}")
                return "update"  # Replace conflicting currency label
        
        # Line-item component labels can coexist, but check for duplicates
        elif new_label_type in line_item_types:
            # These can coexist with currency labels, but not duplicate their own type
            if new_label_type in valid_types:
                return "skip"  # Same VALID line-item component already exists
        
        return "add"  # No conflicts, safe to add


def display_label_update_results(results: List[LabelUpdateResult]):
    """Display a summary of label update results."""
    
    print("\n" + "=" * 80)
    print("📝 LABEL UPDATE RESULTS")
    print("=" * 80)
    
    # Summary statistics
    actions = {}
    for result in results:
        action = result.action_taken
        actions[action] = actions.get(action, 0) + 1
    
    print("Summary:")
    for action, count in sorted(actions.items()):
        print(f"  {action}: {count}")
    
    print(f"\nTotal labels processed: {len(results)}")
    print()
    
    # Detailed results
    for i, result in enumerate(results, 1):
        print(f"Label {i}: {result.currency_label.label_type.value} - ${result.currency_label.value:.2f}")
        
        if result.word:
            print(f"  Word: '{result.word.text}' (line {result.word.line_id}, word {result.word.word_id})")
        else:
            print(f"  Word: Not found")
            
        print(f"  Action: {result.action_taken}")
        
        if result.conflict_reason:
            print(f"  Reason: {result.conflict_reason}")
            
        if result.existing_labels:
            existing_info = []
            for label in result.existing_labels:
                if label.label_consolidated_from:
                    existing_info.append(f"{label.label_consolidated_from}→{label.label}")
                else:
                    existing_info.append(label.label)
            print(f"  Existing labels: {', '.join(existing_info)}")
        
        print()
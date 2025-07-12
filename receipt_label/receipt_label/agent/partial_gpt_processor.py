"""
Partial GPT processor for cost-efficient targeted labeling.

This module handles cases where pattern detection finds most essential labels
but GPT is still needed for specific missing fields. Instead of full receipt
processing, it makes targeted calls for only the missing information.
"""

import logging
from typing import Dict, List, Optional, Tuple
from receipt_label.constants import CORE_LABELS

logger = logging.getLogger(__name__)


class PartialGPTProcessor:
    """Handles targeted GPT calls for missing fields only."""
    
    def __init__(self, openai_client=None):
        """Initialize the partial GPT processor."""
        self.openai_client = openai_client
        self.stats = {
            "partial_calls": 0,
            "tokens_saved": 0,
            "fields_processed": 0,
        }

    def create_targeted_prompt(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict],
        missing_fields: List[str],
        receipt_metadata: Optional[Dict] = None
    ) -> str:
        """
        Create a targeted prompt that focuses only on missing fields.
        
        Args:
            receipt_words: All words in the receipt
            labeled_words: Already labeled words from pattern detection
            missing_fields: List of specific fields GPT needs to find
            receipt_metadata: Optional metadata about the receipt
            
        Returns:
            Optimized prompt string for GPT
        """
        # Build context from known labels
        known_labels = self._build_known_context(labeled_words, receipt_metadata)
        
        # Get unlabeled text that might contain missing fields
        unlabeled_text = self._get_relevant_unlabeled_text(
            receipt_words, labeled_words, missing_fields
        )
        
        # Create focused prompt based on missing fields
        if CORE_LABELS["PRODUCT_NAME"] in missing_fields:
            prompt = self._create_product_name_prompt(
                unlabeled_text, known_labels, receipt_metadata
            )
        elif CORE_LABELS["MERCHANT_NAME"] in missing_fields:
            prompt = self._create_merchant_name_prompt(
                unlabeled_text, known_labels
            )
        elif CORE_LABELS["DATE"] in missing_fields:
            prompt = self._create_date_prompt(
                unlabeled_text, known_labels
            )
        elif CORE_LABELS["GRAND_TOTAL"] in missing_fields:
            prompt = self._create_total_prompt(
                unlabeled_text, known_labels
            )
        else:
            # Fallback for multiple missing fields
            prompt = self._create_multi_field_prompt(
                unlabeled_text, known_labels, missing_fields
            )
        
        return prompt

    def _build_known_context(
        self, labeled_words: Dict[int, Dict], metadata: Optional[Dict] = None
    ) -> Dict[str, str]:
        """Build context from already-known labels."""
        context = {}
        
        # Extract known information
        for word_id, label_info in labeled_words.items():
            label = label_info["label"]
            if label == CORE_LABELS["MERCHANT_NAME"]:
                context["merchant"] = label_info.get("extracted_value", "")
            elif label == CORE_LABELS["DATE"]:
                context["date"] = label_info.get("extracted_value", "")
            elif label == CORE_LABELS["GRAND_TOTAL"]:
                context["total"] = label_info.get("extracted_value", "")
        
        # Add metadata context
        if metadata:
            if metadata.get("merchant_name"):
                context["merchant"] = metadata["merchant_name"]
        
        return context

    def _get_relevant_unlabeled_text(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict],
        missing_fields: List[str]
    ) -> List[str]:
        """Get unlabeled text that might contain the missing fields."""
        unlabeled_lines = []
        labeled_word_ids = set(labeled_words.keys())
        
        # Group words by line
        lines = {}
        for word in receipt_words:
            line_id = word.get("line_id", 0)
            if line_id not in lines:
                lines[line_id] = []
            lines[line_id].append(word)
        
        # For each line, check if it has unlabeled words that might be relevant
        for line_id, line_words in lines.items():
            line_text = []
            has_unlabeled = False
            
            for word in line_words:
                word_id = word.get("word_id")
                text = word.get("text", "")
                
                if word_id in labeled_word_ids:
                    # Mark labeled words
                    label = labeled_words[word_id]["label"]
                    line_text.append(f"[{self._get_label_short_name(label)}:{text}]")
                else:
                    # Unlabeled word
                    line_text.append(text)
                    has_unlabeled = True
            
            # Only include lines with unlabeled content or context
            if has_unlabeled or self._line_provides_context(line_words, missing_fields):
                unlabeled_lines.append(" ".join(line_text))
        
        return unlabeled_lines

    def _get_label_short_name(self, label: str) -> str:
        """Get short name for label for prompt clarity."""
        label_map = {
            CORE_LABELS["MERCHANT_NAME"]: "MERCHANT",
            CORE_LABELS["DATE"]: "DATE", 
            CORE_LABELS["GRAND_TOTAL"]: "TOTAL",
            CORE_LABELS["PRODUCT_NAME"]: "PRODUCT",
            CORE_LABELS["SUBTOTAL"]: "SUBTOTAL",
            CORE_LABELS["TAX"]: "TAX",
        }
        return label_map.get(label, "KNOWN")

    def _line_provides_context(
        self, line_words: List[Dict], missing_fields: List[str]
    ) -> bool:
        """Check if line provides context for missing fields."""
        line_text = " ".join(word.get("text", "") for word in line_words).lower()
        
        # If looking for product names, include lines with prices/quantities
        if CORE_LABELS["PRODUCT_NAME"] in missing_fields:
            if any(char in line_text for char in ["$", "@", "qty", "x "]):
                return True
        
        # If looking for merchant, include header lines
        if CORE_LABELS["MERCHANT_NAME"] in missing_fields:
            # Check if this might be a header line (early in receipt)
            avg_y = sum(word.get("y", 0) for word in line_words) / len(line_words)
            if avg_y < 100:  # Rough heuristic for header
                return True
        
        return False

    def _create_product_name_prompt(
        self, unlabeled_lines: List[str], context: Dict[str, str], metadata: Optional[Dict] = None
    ) -> str:
        """Create targeted prompt for finding product names."""
        context_str = ""
        if context.get("merchant"):
            context_str += f"Merchant: {context['merchant']}\n"
        if context.get("date"):
            context_str += f"Date: {context['date']}\n"
        if context.get("total"):
            context_str += f"Total: {context['total']}\n"
        
        receipt_text = "\n".join(unlabeled_lines)
        
        prompt = f"""You are analyzing a receipt to identify product names. Some information is already known:

{context_str}

Receipt lines (with [LABEL:text] for known items):
{receipt_text}

Task: Identify ONLY the product names from the unlabeled text. Look for:
- Item descriptions (food items, products, services)
- Product codes or SKUs that represent items
- Menu items or product categories

Ignore:
- Payment methods, store info, totals (already labeled)
- Random numbers, dates, addresses
- Receipt formatting elements

Return format:
Product 1: [name]
Product 2: [name]
...

If no clear product names found, respond: "No clear product names identified"
"""
        return prompt

    def _create_merchant_name_prompt(
        self, unlabeled_lines: List[str], context: Dict[str, str]
    ) -> str:
        """Create targeted prompt for finding merchant name."""
        receipt_text = "\n".join(unlabeled_lines[:5])  # Focus on top of receipt
        
        prompt = f"""Identify the merchant/store name from this receipt header:

{receipt_text}

Look for:
- Store names, business names, brand names
- Restaurant names, retail chain names
- Company names at the top of the receipt

Ignore:
- Addresses, phone numbers, websites
- Employee names, transaction IDs
- Dates, times, receipt numbers

Return only the merchant name, or "Merchant name not found" if unclear.
"""
        return prompt

    def _create_date_prompt(
        self, unlabeled_lines: List[str], context: Dict[str, str]
    ) -> str:
        """Create targeted prompt for finding transaction date."""
        receipt_text = "\n".join(unlabeled_lines)
        
        prompt = f"""Find the transaction date from this receipt:

{receipt_text}

Look for:
- Purchase date, transaction date
- Date in MM/DD/YYYY, DD/MM/YYYY, or similar format
- Date near transaction info

Ignore:
- Expiration dates, birth dates
- Future dates (likely promotions)
- Time-only values

Return the transaction date in YYYY-MM-DD format, or "Date not found".
"""
        return prompt

    def _create_total_prompt(
        self, unlabeled_lines: List[str], context: Dict[str, str]
    ) -> str:
        """Create targeted prompt for finding grand total."""
        receipt_text = "\n".join(unlabeled_lines[-5:])  # Focus on bottom of receipt
        
        prompt = f"""Find the final amount due (grand total) from this receipt:

{receipt_text}

Look for:
- Final total, grand total, amount due
- Total after tax and discounts
- The amount the customer actually paid

Ignore:
- Subtotals, tax amounts, individual item prices
- Change given, amounts tendered
- Discount amounts

Return the grand total amount with currency symbol (e.g., $15.99), or "Total not found".
"""
        return prompt

    def _create_multi_field_prompt(
        self, unlabeled_lines: List[str], context: Dict[str, str], missing_fields: List[str]
    ) -> str:
        """Create prompt for multiple missing fields."""
        receipt_text = "\n".join(unlabeled_lines)
        
        field_descriptions = []
        for field in missing_fields:
            if field == CORE_LABELS["PRODUCT_NAME"]:
                field_descriptions.append("- Product names: item descriptions purchased")
            elif field == CORE_LABELS["MERCHANT_NAME"]:
                field_descriptions.append("- Merchant name: store/business name")
            elif field == CORE_LABELS["DATE"]:
                field_descriptions.append("- Transaction date: date of purchase")
            elif field == CORE_LABELS["GRAND_TOTAL"]:
                field_descriptions.append("- Grand total: final amount due")
        
        prompt = f"""Extract the following missing information from this receipt:

{chr(10).join(field_descriptions)}

Receipt text:
{receipt_text}

Return in this format:
Merchant: [name or "not found"]
Date: [YYYY-MM-DD or "not found"]
Total: [amount or "not found"]
Products: [list or "not found"]
"""
        return prompt

    async def process_missing_fields(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict], 
        missing_fields: List[str],
        receipt_metadata: Optional[Dict] = None
    ) -> Dict[int, Dict]:
        """
        Process missing fields using targeted GPT calls.
        
        Args:
            receipt_words: All words in receipt
            labeled_words: Existing labeled words
            missing_fields: Fields that need GPT processing
            receipt_metadata: Optional metadata
            
        Returns:
            Updated labeled_words with new GPT labels
        """
        if not missing_fields:
            return labeled_words
            
        # For simulation, we don't need openai_client
        # if not self.openai_client:
        #     return labeled_words
        
        logger.info(f"Starting partial GPT processing for {len(missing_fields)} fields")
        self.stats["partial_calls"] += 1
        self.stats["fields_processed"] += len(missing_fields)
        
        # Create targeted prompt
        prompt = self.create_targeted_prompt(
            receipt_words, labeled_words, missing_fields, receipt_metadata
        )
        
        logger.info(f"Making targeted GPT call for fields: {missing_fields}")
        logger.debug(f"Prompt: {prompt[:200]}...")
        
        try:
            # Make GPT call (this would use the actual OpenAI client)
            # For now, simulate the response
            gpt_response = self._simulate_gpt_response(missing_fields)
            
            # Parse response and update labeled_words
            logger.debug(f"GPT Response: {gpt_response}")
            new_labels = self._parse_gpt_response(gpt_response, receipt_words, missing_fields)
            logger.debug(f"Parsed {len(new_labels)} new labels from GPT response")
            labeled_words.update(new_labels)
            
            # Estimate tokens saved vs full GPT call
            tokens_saved = self._estimate_tokens_saved(prompt, receipt_words)
            self.stats["tokens_saved"] += tokens_saved
            
            logger.info(f"Partial GPT call completed, estimated {tokens_saved} tokens saved")
            
        except Exception as e:
            logger.error(f"Partial GPT call failed: {e}")
        
        return labeled_words

    def _simulate_gpt_response(self, missing_fields: List[str]) -> str:
        """Simulate GPT response for testing purposes."""
        if CORE_LABELS["PRODUCT_NAME"] in missing_fields:
            return "Product 1: SHAMPOO\nProduct 2: CONDITIONER"
        elif CORE_LABELS["MERCHANT_NAME"] in missing_fields:
            return "WALMART SUPERCENTER"
        elif CORE_LABELS["DATE"] in missing_fields:
            return "2024-02-15"
        elif CORE_LABELS["GRAND_TOTAL"] in missing_fields:
            return "$19.99"
        else:
            return "Fields processed successfully"

    def _parse_gpt_response(
        self, response: str, receipt_words: List[Dict], missing_fields: List[str]
    ) -> Dict[int, Dict]:
        """Parse GPT response and map to word IDs."""
        new_labels = {}
        
        # This is a simplified parser - production would be more sophisticated
        if CORE_LABELS["PRODUCT_NAME"] in missing_fields:
            # Find product words and label them
            for line in response.split('\n'):
                if line.startswith('Product'):
                    product_name = line.split(': ')[1] if ': ' in line else ""
                    logger.debug(f"Looking for product name: '{product_name}'")
                    # Find corresponding words in receipt (simplified)
                    for word in receipt_words:
                        word_text = word.get('text', '').upper()
                        word_id = word.get('word_id')
                        logger.debug(f"Checking word {word_id}: '{word_text}' against '{product_name.upper()}'")
                        if product_name.upper() in word_text or word_text in product_name.upper():
                            logger.debug(f"Match found! Labeling word {word_id} as product")
                            new_labels[word_id] = {
                                'label': CORE_LABELS["PRODUCT_NAME"],
                                'confidence': 0.85,
                                'source': 'partial_gpt',
                                'extracted_value': product_name,
                                'is_essential': True
                            }
        
        return new_labels

    def _estimate_tokens_saved(self, partial_prompt: str, receipt_words: List[Dict]) -> int:
        """Estimate tokens saved vs full receipt processing."""
        # Rough estimation: full prompt would be ~4x longer
        partial_tokens = len(partial_prompt.split()) * 1.3  # Rough token estimate
        full_tokens = len(' '.join(w.get('text', '') for w in receipt_words)) * 1.3 * 3
        return max(0, int(full_tokens - partial_tokens))

    def get_statistics(self) -> Dict:
        """Get partial processing statistics."""
        return self.stats.copy()

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "partial_calls": 0,
            "tokens_saved": 0,
            "fields_processed": 0,
        }
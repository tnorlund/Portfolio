from typing import Dict, List, Optional
from decimal import Decimal
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from ..data.gpt import gpt_request_line_item_analysis

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set default level to INFO

class LineItemProcessor:
    """Processes line items in receipts using GPT."""

    def __init__(self, gpt_api_key: Optional[str] = None):
        """Initialize the processor with optional GPT API key."""
        self.gpt_api_key = gpt_api_key

    async def process_line_items(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict] = None,
    ) -> LineItemAnalysis:
        """
        Process line items in the receipt using GPT analysis.
        
        Args:
            receipt: Receipt object
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            places_api_data: Optional Places API data for business context

        Returns:
            LineItemAnalysis containing the processed line items
        """
        logger.debug("Starting line item processing for receipt %s", receipt.receipt_id)
        logger.debug("Number of receipt lines: %d", len(receipt_lines))
        logger.debug("Number of receipt words: %d", len(receipt_words))
        
        # First do traditional analysis to provide context to GPT
        traditional_analysis = self._extract_traditional_analysis(receipt_lines, receipt_words)
        logger.debug("Traditional analysis results: %s", traditional_analysis)
        
        # Get GPT analysis
        try:
            logger.debug("Calling GPT for line item analysis...")
            gpt_result, query, raw_response = gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                traditional_analysis=traditional_analysis,
                places_api_data=places_api_data,
                gpt_api_key=self.gpt_api_key
            )
            logger.debug(f"GPT query: {query}")
            logger.debug(f"Raw GPT response: {raw_response}")
            logger.debug(f"Processed GPT result: {gpt_result}")
            
            # Convert GPT results to LineItemAnalysis
            items = []
            for item_data in gpt_result["line_items"]:
                try:
                    logger.debug(f"Processing line item: {item_data}")
                    quantity = None
                    if item_data.get("quantity"):
                        try:
                            quantity = Quantity(
                                amount=Decimal(str(item_data["quantity"]["amount"])),
                                unit=item_data["quantity"]["unit"]
                            )
                            logger.debug(f"Extracted quantity: {quantity}")
                        except (KeyError, ValueError) as e:
                            logger.warning(f"Error processing quantity: {e}")

                    price = None
                    if item_data.get("price"):
                        try:
                            price = Price(
                                unit_price=Decimal(str(item_data["price"]["unit_price"])) if item_data["price"].get("unit_price") else None,
                                extended_price=Decimal(str(item_data["price"]["extended_price"])) if item_data["price"].get("extended_price") else None
                            )
                            logger.debug(f"Extracted price: {price}")
                        except (KeyError, ValueError) as e:
                            logger.warning(f"Error processing price: {e}")

                    item = LineItem(
                        description=item_data["description"],
                        quantity=quantity,
                        price=price,
                        confidence=item_data["confidence"],
                        line_ids=item_data["line_ids"]
                    )
                    logger.debug(f"Created line item: {item}")
                    items.append(item)
                except Exception as e:
                    logger.error(f"Error processing individual line item: {str(e)}")

            analysis = gpt_result.get("analysis", {})
            logger.debug(f"Analysis data: {analysis}")
            
            logger.debug(f"Found {len(items)} line items with total: {analysis.get('total', 0)}")
            
            result = LineItemAnalysis(
                items=items,
                total_found=analysis.get("total_found", len(items)),
                subtotal=Decimal(str(analysis["subtotal"])) if analysis.get("subtotal") else None,
                tax=Decimal(str(analysis["tax"])) if analysis.get("tax") else None,
                total=Decimal(str(analysis["total"])) if analysis.get("total") else None,
                discrepancies=analysis.get("discrepancies", []),
                confidence=analysis.get("confidence", 0.0)
            )
            logger.debug(f"Final line item analysis: {result}")
            return result

        except Exception as e:
            logger.error(f"Error in GPT line item processing: {str(e)}", exc_info=True)
            # Return empty analysis on error
            return LineItemAnalysis(
                items=[],
                total_found=0,
                subtotal=None,
                tax=None,
                total=None,
                discrepancies=[f"Error in GPT processing: {str(e)}"],
                confidence=0.0
            )

    def _extract_traditional_analysis(
        self,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord]
    ) -> Dict:
        """Extract basic totals and patterns for GPT context."""
        logger.debug("Starting traditional analysis...")
        totals = {
            "total": None,
            "subtotal": None,
            "tax": None,
            "fees": None,
            "tips": None,
            "discounts": None
        }

        try:
            # Look for total-related labels
            for word in receipt_words:
                text = word.text.lower()
                if any(label in text for label in ["total", "subtotal", "tax", "fee", "tip", "discount"]):
                    logger.debug(f"Found potential total label: {word.text}")
                    # Look for associated amount on same line
                    line_words = [w for w in receipt_words if w.line_id == word.line_id]
                    logger.debug(f"Words on same line: {[w.text for w in line_words]}")
                    for line_word in line_words:
                        if self._is_likely_amount(line_word.text):
                            amount = self._extract_amount(line_word.text)
                            if amount:
                                logger.debug(f"Found amount {amount} for label {text}")
                                if "total" in text and "sub" not in text:
                                    totals["total"] = amount
                                elif "subtotal" in text:
                                    totals["subtotal"] = amount
                                elif "tax" in text:
                                    totals["tax"] = amount
                                elif "fee" in text:
                                    totals["fees"] = amount
                                elif "tip" in text:
                                    totals["tips"] = amount
                                elif "discount" in text:
                                    totals["discounts"] = amount

            logger.debug(f"Extracted totals: {totals}")
            return {"totals": totals}
        except Exception as e:
            logger.error(f"Error in traditional analysis: {str(e)}", exc_info=True)
            return {"totals": totals}

    def _is_likely_amount(self, text: str) -> bool:
        """Check if text looks like a monetary amount."""
        text = text.strip()
        is_amount = (
            text.replace(".", "").replace(",", "").replace("$", "").replace("-", "").isdigit()
            and "." in text
        )
        logger.debug(f"Checking if '{text}' is an amount: {is_amount}")
        return is_amount

    def _extract_amount(self, text: str) -> Optional[Decimal]:
        """Extract decimal amount from text."""
        try:
            # Remove currency symbols and other non-numeric chars except decimal point
            clean_text = "".join(c for c in text if c.isdigit() or c == ".")
            amount = Decimal(clean_text)
            logger.debug(f"Extracted amount {amount} from text '{text}'")
            return amount
        except Exception as e:
            logger.debug(f"Failed to extract amount from '{text}': {str(e)}")
            return None 
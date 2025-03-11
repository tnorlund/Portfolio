from typing import Dict, List, Optional, Tuple
from decimal import Decimal, InvalidOperation
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from ..models.uncertainty import (
    MultipleAmountsUncertainty, MissingComponentUncertainty, 
    TotalMismatchUncertainty, UncertaintyItem, ensure_decimal
)
from ..data.gpt import gpt_request_line_item_analysis

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class LLMProcessor:
    """Handles processing of uncertain items using LLM."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        self.gpt_api_key = gpt_api_key
    
    async def process_uncertain_items(
        self,
        uncertain_items: List[UncertaintyItem],
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        initial_state: Dict,
        places_api_data: Dict,
    ) -> Optional[Dict]:
        """Process uncertain items using LLM analysis."""
        if not uncertain_items:
            return None
            
        updates = {}
        
        # Group uncertain items by type
        missing_components = [
            item for item in uncertain_items 
            if isinstance(item, MissingComponentUncertainty)
        ]
        multiple_amounts = [
            item for item in uncertain_items 
            if isinstance(item, MultipleAmountsUncertainty)
        ]
        total_mismatches = [
            item for item in uncertain_items 
            if isinstance(item, TotalMismatchUncertainty)
        ]
        
        # Process missing components
        if missing_components:
            try:
                logger.info(f"Processing {len(missing_components)} missing components")
                component_updates = await self._process_missing_components(
                    missing_components,
                    receipt,
                    receipt_lines,
                    receipt_words,
                    initial_state,
                    places_api_data
                )
                if component_updates:
                    updates.update(component_updates)
            except Exception as e:
                logger.error(f"Error in LLM processing of missing components: {str(e)}")
        
        # Process items with multiple amounts
        if multiple_amounts:
            try:
                logger.info(f"Processing {len(multiple_amounts)} items with multiple amounts")
                amount_updates = await self._process_multiple_amounts(
                    multiple_amounts,
                    receipt,
                    receipt_lines,
                    receipt_words,
                    initial_state,
                    places_api_data
                )
                if amount_updates:
                    updates.update(amount_updates)
            except Exception as e:
                logger.error(f"Error in LLM processing of multiple amounts: {str(e)}")
        
        # Process total mismatches
        if total_mismatches:
            try:
                logger.info(f"Processing {len(total_mismatches)} total mismatches")
                mismatch_updates = await self._process_total_mismatches(
                    total_mismatches,
                    receipt,
                    receipt_lines,
                    receipt_words,
                    initial_state,
                    places_api_data
                )
                if mismatch_updates:
                    updates.update(mismatch_updates)
            except Exception as e:
                logger.error(f"Error in LLM processing of total mismatches: {str(e)}")
        
        return updates if updates else None
    
    def _serialize_for_gpt(self, obj: Dict) -> Dict:
        """Convert objects to JSON-serializable format."""
        if isinstance(obj, dict):
            return {k: self._serialize_for_gpt(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_for_gpt(item) for item in list(obj)]
        elif hasattr(obj, '__dict__'):
            # Handle custom objects by converting them to dictionaries
            serialized = {}
            for k, v in obj.__dict__.items():
                if isinstance(v, Decimal):
                    serialized[k] = str(v)
                else:
                    serialized[k] = self._serialize_for_gpt(v)
            return serialized
        elif isinstance(obj, Decimal):
            return str(obj)
        return obj

    async def _process_missing_components(
        self,
        missing_components: List[MissingComponentUncertainty],
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        current_results: Dict,
        places_api_data: Dict
    ) -> Dict:
        """Process missing components using LLM analysis."""
        
        # Prepare context for GPT
        context = {
            "receipt_text": "\n".join(line.text for line in receipt_lines),
            "current_results": self._serialize_for_gpt(current_results),
            "missing_components": [item.component for item in missing_components]
        }
        
        try:
            # Call GPT for analysis
            gpt_result, _, _ = await gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                traditional_analysis=context,
                places_api_data=places_api_data,
                gpt_api_key=self.gpt_api_key
            )
            
            # Extract updates from GPT response
            updates = {}
            analysis = gpt_result.get("analysis", {})
            logger.debug(f"Received analysis from GPT: {analysis}")
            
            for field in ["subtotal", "tax", "total"]:
                value = analysis.get(field)
                if value is not None:  # Only try to convert if we actually got a value
                    try:
                        logger.debug(f"Converting {field} value: {value} (type: {type(value)})")
                        updates[field] = ensure_decimal(value)
                    except ValueError as e:
                        logger.error(f"Failed to convert {field} value '{value}': {str(e)}")
                else:
                    logger.debug(f"No value found for {field}")
            
            return updates
            
        except Exception as e:
            logger.error(f"Error in LLM processing of missing components: {str(e)}")
            return {}
    
    async def _process_multiple_amounts(
        self,
        multiple_amounts: List[MultipleAmountsUncertainty],
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        current_results: Dict,
        places_api_data: Dict
    ) -> Dict:
        """Process items with multiple amounts using LLM analysis."""
        
        # Prepare context for GPT
        context = {
            "receipt_text": "\n".join(line.text for line in receipt_lines),
            "current_results": self._serialize_for_gpt(current_results),
            "multiple_amount_lines": [
                {
                    "text": item.line.text,
                    "line_id": item.line.line_id,
                    "amounts": [str(amount) for amount in item.amounts]
                }
                for item in multiple_amounts
            ]
        }
        
        try:
            # Call GPT for analysis
            gpt_result, _, _ = await gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                traditional_analysis=context,
                places_api_data=places_api_data,
                gpt_api_key=self.gpt_api_key
            )
            
            # Process GPT response
            updates = {"line_items": []}
            
            for item_data in gpt_result.get("line_items", []):
                if "line_ids" not in item_data:
                    continue
                    
                try:
                    quantity = None
                    if item_data.get("quantity"):
                        quantity = Quantity(
                            amount=Decimal(str(item_data["quantity"]["amount"])),
                            unit=item_data["quantity"]["unit"]
                        )
                    
                    price = None
                    if item_data.get("price"):
                        price = Price(
                            unit_price=Decimal(str(item_data["price"]["unit_price"])) if item_data["price"].get("unit_price") else None,
                            extended_price=Decimal(str(item_data["price"]["extended_price"])) if item_data["price"].get("extended_price") else None
                        )
                    
                    item = LineItem(
                        description=item_data["description"],
                        quantity=quantity,
                        price=price,
                        reasoning=item_data.get("reasoning", "Generated via LLM analysis"),
                        line_ids=item_data["line_ids"]
                    )
                    updates["line_items"].append(item)
                    
                except (KeyError, ValueError) as e:
                    logger.warning(f"Error processing line item from LLM: {str(e)}")
                    continue
            
            return updates
            
        except Exception as e:
            logger.error(f"Error in LLM processing of multiple amounts: {str(e)}")
            return {}
    
    async def _process_total_mismatches(
        self,
        total_mismatches: List[TotalMismatchUncertainty],
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        current_results: Dict,
        places_api_data: Dict
    ) -> Dict:
        """Process total mismatches using LLM analysis."""
        
        # Prepare context for GPT
        context = {
            "receipt_text": "\n".join(line.text for line in receipt_lines),
            "current_results": self._serialize_for_gpt(current_results),
            "mismatches": [
                {
                    "calculated": str(item.calculated),
                    "found": str(item.found),
                    "difference": str(abs(item.calculated - item.found))
                }
                for item in total_mismatches
            ]
        }
        
        try:
            # Call GPT for analysis
            gpt_result, _, _ = await gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                traditional_analysis=context,
                places_api_data=places_api_data,
                gpt_api_key=self.gpt_api_key
            )
            
            # Extract updates from GPT response
            updates = {}
            analysis = gpt_result.get("analysis", {})
            
            if "subtotal" in analysis:
                updates["subtotal"] = Decimal(str(analysis["subtotal"]))
            if "tax" in analysis:
                updates["tax"] = Decimal(str(analysis["tax"]))
            if "total" in analysis:
                updates["total"] = Decimal(str(analysis["total"]))
            
            return updates
            
        except Exception as e:
            logger.error(f"Error in LLM processing of total mismatches: {str(e)}")
            return {} 
"""
Merchant-specific essential label requirements for intelligent GPT decisions.

This module defines different essential label requirements based on merchant category,
allowing the decision engine to make more intelligent choices about when GPT is needed.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from dataclasses import dataclass

from receipt_label.constants import CORE_LABELS

logger = logging.getLogger(__name__)


@dataclass
class EssentialLabelConfig:
    """Configuration for essential labels by tier."""
    core_labels: Set[str]
    secondary_labels: Set[str]
    description: str
    rationale: str

    @property
    def all_labels(self) -> Set[str]:
        """Get all essential labels (core + secondary)."""
        return self.core_labels | self.secondary_labels


class MerchantEssentialLabels:
    """
    Manages merchant-specific essential label requirements.
    
    This system allows different merchant categories to have different sets of
    essential labels, enabling smarter GPT decisions based on business context.
    """
    
    def __init__(self):
        """Initialize merchant-specific essential label configurations."""
        self.category_configs = self._initialize_category_configs()
        self.default_config = self._get_default_config()
        
        # Statistics
        self.stats = {
            "category_overrides_used": 0,
            "default_config_used": 0,
            "categories_supported": len(self.category_configs)
        }
    
    def _initialize_category_configs(self) -> Dict[str, EssentialLabelConfig]:
        """Initialize essential label configurations by merchant category."""
        return {
            "restaurant": EssentialLabelConfig(
                core_labels={
                    CORE_LABELS["MERCHANT_NAME"],
                    CORE_LABELS["DATE"],
                    CORE_LABELS["GRAND_TOTAL"],
                },
                secondary_labels={
                    # Restaurants often have simple product names that are less critical
                    # Focus on payment and basic transaction info instead
                    CORE_LABELS["PAYMENT_METHOD"],
                },
                description="Restaurant/fast food essential labels",
                rationale="Restaurants typically have simple product structures. "
                         "Focus on transaction basics rather than detailed product info."
            ),
            
            "grocery": EssentialLabelConfig(
                core_labels={
                    CORE_LABELS["MERCHANT_NAME"],
                    CORE_LABELS["DATE"], 
                    CORE_LABELS["GRAND_TOTAL"],
                },
                secondary_labels={
                    # Grocery stores need product details for expense tracking
                    CORE_LABELS["PRODUCT_NAME"],
                    CORE_LABELS["QUANTITY"],
                    CORE_LABELS["TAX"],
                },
                description="Grocery store essential labels",
                rationale="Grocery receipts require detailed product information for "
                         "expense categorization and tax reporting."
            ),
            
            "retail": EssentialLabelConfig(
                core_labels={
                    CORE_LABELS["MERCHANT_NAME"],
                    CORE_LABELS["DATE"],
                    CORE_LABELS["GRAND_TOTAL"],
                },
                secondary_labels={
                    # Retail purchases often need product details and payment info
                    CORE_LABELS["PRODUCT_NAME"],
                    CORE_LABELS["PAYMENT_METHOD"],
                },
                description="Retail store essential labels",
                rationale="Retail receipts need product identification and payment "
                         "method for warranty and return purposes."
            ),
            
            "pharmacy": EssentialLabelConfig(
                core_labels={
                    CORE_LABELS["MERCHANT_NAME"],
                    CORE_LABELS["DATE"],
                    CORE_LABELS["GRAND_TOTAL"],
                },
                secondary_labels={
                    # Pharmacies have regulatory requirements for detailed records
                    CORE_LABELS["PRODUCT_NAME"],
                    CORE_LABELS["QUANTITY"],
                    CORE_LABELS["PHONE_NUMBER"],  # For prescription verification
                },
                description="Pharmacy essential labels",
                rationale="Pharmacy receipts require detailed product and contact "
                         "information for regulatory compliance and prescription tracking."
            ),
            
            "gas_station": EssentialLabelConfig(
                core_labels={
                    CORE_LABELS["MERCHANT_NAME"],
                    CORE_LABELS["DATE"],
                    CORE_LABELS["GRAND_TOTAL"],
                },
                secondary_labels={
                    # Gas stations typically have simple transactions
                    # Product name is often just "Regular Gas" or similar
                    CORE_LABELS["PAYMENT_METHOD"],
                },
                description="Gas station essential labels",
                rationale="Gas station receipts are typically simple transactions. "
                         "Product names are often generic (gas type), so focus on "
                         "transaction basics and payment method."
            ),
        }
    
    def _get_default_config(self) -> EssentialLabelConfig:
        """Get default essential label configuration for unknown merchants."""
        return EssentialLabelConfig(
            core_labels={
                CORE_LABELS["MERCHANT_NAME"],
                CORE_LABELS["DATE"],
                CORE_LABELS["GRAND_TOTAL"],
            },
            secondary_labels={
                CORE_LABELS["PRODUCT_NAME"],
            },
            description="Default essential labels for unknown merchants",
            rationale="Conservative approach requiring basic transaction info "
                     "and at least one product detail for unknown merchant types."
        )
    
    def get_essential_labels_for_merchant(
        self, 
        merchant_name: Optional[str] = None, 
        merchant_category: Optional[str] = None
    ) -> Tuple[EssentialLabelConfig, str]:
        """
        Get essential label configuration for a specific merchant.
        
        Args:
            merchant_name: Name of the merchant (used for category detection if needed)
            merchant_category: Pre-determined merchant category
            
        Returns:
            Tuple of:
            - EssentialLabelConfig for the merchant
            - Category used (for logging/debugging)
        """
        # Use provided category or detect from merchant name
        if merchant_category:
            category = merchant_category
        elif merchant_name:
            category = self._detect_merchant_category(merchant_name)
        else:
            category = "unknown"
        
        # Get configuration for category
        if category in self.category_configs:
            config = self.category_configs[category]
            self.stats["category_overrides_used"] += 1
            logger.debug(f"Using {category} essential labels for {merchant_name}")
        else:
            config = self.default_config
            self.stats["default_config_used"] += 1
            logger.debug(f"Using default essential labels for {merchant_name} (category: {category})")
        
        return config, category
    
    def _detect_merchant_category(self, merchant_name: str) -> str:
        """
        Detect merchant category based on name.
        
        This is a simplified version that mirrors the logic in MerchantPatternDatabase.
        In production, this could be enhanced to use the same detection logic.
        """
        merchant_lower = merchant_name.lower()
        
        # Restaurant indicators
        restaurant_keywords = [
            "restaurant", "cafe", "coffee", "pizza", "burger", "chicken", "taco",
            "mcdonald", "burger king", "kfc", "taco bell", "subway", "chipotle",
            "starbucks", "dunkin", "domino", "papa john", "wendy", "arby",
            "dairy queen", "sonic", "ihop", "denny", "applebee", "olive garden"
        ]
        
        # Grocery indicators
        grocery_keywords = [
            "grocery", "market", "supermarket", "walmart", "target", "kroger",
            "safeway", "albertsons", "publix", "wegmans", "whole foods", "trader joes",
            "costco", "sams club", "aldi", "food lion", "giant", "stop shop"
        ]
        
        # Retail indicators
        retail_keywords = [
            "store", "shop", "retail", "mall", "outlet", "amazon", "best buy",
            "home depot", "lowes", "macys", "nordstrom", "tj maxx", "ross",
            "marshalls", "old navy", "gap", "american eagle", "forever 21"
        ]
        
        # Pharmacy indicators
        pharmacy_keywords = [
            "pharmacy", "drug", "cvs", "walgreens", "rite aid", "care pharmacy"
        ]
        
        # Gas station indicators
        gas_keywords = [
            "gas", "fuel", "shell", "exxon", "bp", "chevron", "mobil", "sunoco",
            "circle k", "7-eleven", "wawa", "speedway", "marathon"
        ]
        
        # Check categories in order of specificity
        if any(keyword in merchant_lower for keyword in pharmacy_keywords):
            return "pharmacy"
        elif any(keyword in merchant_lower for keyword in gas_keywords):
            return "gas_station"
        elif any(keyword in merchant_lower for keyword in restaurant_keywords):
            return "restaurant"
        elif any(keyword in merchant_lower for keyword in grocery_keywords):
            return "grocery"
        elif any(keyword in merchant_lower for keyword in retail_keywords):
            return "retail"
        else:
            return "unknown"
    
    def get_all_categories(self) -> Dict[str, EssentialLabelConfig]:
        """Get all available merchant category configurations."""
        return self.category_configs.copy()
    
    def get_category_summary(self) -> Dict[str, Dict]:
        """Get a summary of all category configurations."""
        summary = {}
        
        for category, config in self.category_configs.items():
            summary[category] = {
                "description": config.description,
                "rationale": config.rationale,
                "core_labels": list(config.core_labels),
                "secondary_labels": list(config.secondary_labels),
                "total_essential": len(config.all_labels)
            }
        
        # Add default configuration
        summary["default"] = {
            "description": self.default_config.description,
            "rationale": self.default_config.rationale,
            "core_labels": list(self.default_config.core_labels),
            "secondary_labels": list(self.default_config.secondary_labels),
            "total_essential": len(self.default_config.all_labels)
        }
        
        return summary
    
    def get_statistics(self) -> Dict:
        """Get merchant essential labels statistics."""
        stats = self.stats.copy()
        
        stats.update({
            "total_usage": stats["category_overrides_used"] + stats["default_config_used"],
            "override_rate": (
                stats["category_overrides_used"] / 
                (stats["category_overrides_used"] + stats["default_config_used"])
                if (stats["category_overrides_used"] + stats["default_config_used"]) > 0
                else 0
            )
        })
        
        return stats
    
    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "category_overrides_used": 0,
            "default_config_used": 0,
            "categories_supported": len(self.category_configs)
        }
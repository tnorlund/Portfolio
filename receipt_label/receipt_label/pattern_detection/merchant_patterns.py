"""
Merchant-specific pattern database for enhanced receipt processing.

This module manages merchant-specific keywords and patterns stored in Pinecone,
enabling targeted pattern detection that improves accuracy and reduces GPT costs
by leveraging known merchant-specific terminology and structures.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
import json
import yaml
from pathlib import Path

from receipt_label.constants import CORE_LABELS

logger = logging.getLogger(__name__)


@dataclass
class MerchantPattern:
    """Represents a merchant-specific pattern for receipt processing."""
    pattern_text: str
    label_type: str
    confidence: float
    merchant_name: str
    category: Optional[str] = None
    frequency: int = 1
    last_seen: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MerchantKeywordSet:
    """Collection of keywords for a specific merchant and label type."""
    merchant_name: str
    label_type: str
    keywords: Set[str]
    confidence_scores: Dict[str, float]
    category: Optional[str] = None
    updated_at: datetime = field(default_factory=datetime.now)


class MerchantPatternDatabase:
    """
    Manages merchant-specific patterns and keywords in Pinecone.
    
    This system stores and retrieves merchant-specific terminology to improve
    pattern detection accuracy and reduce dependency on GPT for common patterns.
    """
    
    def __init__(self, pinecone_client=None, namespace_prefix: str = "merchant_patterns", patterns_file: Optional[str] = None):
        """
        Initialize merchant pattern database.
        
        Args:
            pinecone_client: Pinecone index client
            namespace_prefix: Prefix for Pinecone namespaces
            patterns_file: Path to YAML file containing merchant patterns (optional)
        """
        self.pinecone_client = pinecone_client
        self.namespace_prefix = namespace_prefix
        
        # Load merchant patterns from YAML file or use defaults
        self.patterns_file = patterns_file
        self.common_patterns = self._load_patterns_from_file() if patterns_file else self._initialize_common_patterns()
        
        # Load merchant category keywords
        self.merchant_category_keywords = self._load_merchant_category_keywords()
        
        # Statistics
        self.stats = {
            "patterns_stored": 0,
            "patterns_retrieved": 0,
            "cache_hits": 0,
            "merchants_indexed": 0,
        }

    def _load_patterns_from_file(self) -> Dict[str, Dict[str, List[str]]]:
        """Load merchant patterns from YAML file."""
        try:
            # If no specific file provided, use default location
            if not self.patterns_file:
                current_dir = Path(__file__).parent
                default_file = current_dir / "merchant_patterns.yaml"
                if default_file.exists():
                    self.patterns_file = str(default_file)
                else:
                    logger.warning(f"Default patterns file not found at {default_file}")
                    return self._initialize_common_patterns()
            
            # Load YAML file
            with open(self.patterns_file, 'r') as f:
                data = yaml.safe_load(f)
            
            # Extract patterns (exclude merchant_categories section)
            patterns = {}
            for category, labels in data.items():
                if category != "merchant_categories":
                    patterns[category] = {}
                    for label_type, keywords in labels.items():
                        # Map label names to CORE_LABELS constants
                        if label_type in CORE_LABELS.values():
                            patterns[category][label_type] = keywords
                        else:
                            # Try to map by key name
                            for core_key, core_value in CORE_LABELS.items():
                                if core_key == label_type:
                                    patterns[category][core_value] = keywords
                                    break
            
            logger.info(f"Loaded patterns from {self.patterns_file}: {len(patterns)} categories")
            return patterns
            
        except Exception as e:
            logger.error(f"Failed to load patterns from file: {e}")
            return self._initialize_common_patterns()
    
    def _load_merchant_category_keywords(self) -> Dict[str, List[str]]:
        """Load merchant category detection keywords from YAML file."""
        try:
            if self.patterns_file:
                with open(self.patterns_file, 'r') as f:
                    data = yaml.safe_load(f)
                
                if "merchant_categories" in data:
                    logger.info(f"Loaded merchant category keywords from {self.patterns_file}")
                    return data["merchant_categories"]
            
            # Fall back to default keywords
            return self._get_default_category_keywords()
            
        except Exception as e:
            logger.error(f"Failed to load merchant category keywords: {e}")
            return self._get_default_category_keywords()
    
    def _get_default_category_keywords(self) -> Dict[str, List[str]]:
        """Get default merchant category keywords."""
        return {
            "restaurant": [
                "restaurant", "cafe", "coffee", "pizza", "burger", "chicken", "taco",
                "mcdonald", "burger king", "kfc", "taco bell", "subway", "chipotle",
                "starbucks", "dunkin", "domino", "papa john", "wendy", "arby",
                "dairy queen", "sonic", "ihop", "denny", "applebee", "olive garden"
            ],
            "grocery": [
                "grocery", "market", "supermarket", "walmart", "target", "kroger",
                "safeway", "albertsons", "publix", "wegmans", "whole foods", "trader joes",
                "costco", "sams club", "aldi", "food lion", "giant", "stop shop"
            ],
            "retail": [
                "store", "shop", "retail", "mall", "outlet", "amazon", "best buy",
                "home depot", "lowes", "macys", "nordstrom", "tj maxx", "ross",
                "marshalls", "old navy", "gap", "american eagle", "forever 21"
            ],
            "pharmacy": [
                "pharmacy", "drug", "cvs", "walgreens", "rite aid", "care pharmacy"
            ],
            "gas_station": [
                "gas", "fuel", "shell", "exxon", "bp", "chevron", "mobil", "sunoco",
                "circle k", "7-eleven", "wawa", "speedway", "marathon"
            ]
        }
    
    def _initialize_common_patterns(self) -> Dict[str, Dict[str, List[str]]]:
        """Initialize database with common merchant patterns by category."""
        # Try to load from default YAML file first
        current_dir = Path(__file__).parent
        default_file = current_dir / "merchant_patterns.yaml"
        if default_file.exists():
            self.patterns_file = str(default_file)
            return self._load_patterns_from_file()
        
        # Fall back to hardcoded patterns
        return {
            "restaurant": {
                CORE_LABELS["PRODUCT_NAME"]: [
                    # Fast food items
                    "big mac", "whopper", "quarter pounder", "baconator", "dave's single",
                    "crispy chicken", "spicy chicken", "grilled chicken", "chicken nuggets",
                    "french fries", "onion rings", "mozzarella sticks", "chicken strips",
                    "milkshake", "soft drink", "coca cola", "pepsi", "sprite", "dr pepper",
                    
                    # Pizza items
                    "pepperoni pizza", "cheese pizza", "supreme pizza", "margherita pizza",
                    "hawaiian pizza", "meat lovers", "veggie pizza", "breadsticks", "garlic bread",
                    
                    # Coffee/Bakery
                    "latte", "cappuccino", "macchiato", "americano", "espresso", "frappuccino",
                    "croissant", "muffin", "bagel", "danish", "donut", "cookie", "scone",
                    
                    # Casual dining
                    "appetizer", "salad", "soup", "burger", "sandwich", "wrap", "pasta",
                    "steak", "chicken breast", "salmon", "dessert", "ice cream", "cake"
                ],
                CORE_LABELS["TAX"]: [
                    "sales tax", "tax", "state tax", "local tax", "city tax", "meal tax"
                ],
                CORE_LABELS["DISCOUNT"]: [
                    "discount", "coupon", "promotion", "employee discount", "senior discount",
                    "student discount", "military discount", "loyalty reward", "points redemption"
                ]
            },
            
            "grocery": {
                CORE_LABELS["PRODUCT_NAME"]: [
                    # Fresh produce
                    "bananas", "apples", "oranges", "grapes", "strawberries", "blueberries",
                    "lettuce", "tomatoes", "onions", "potatoes", "carrots", "broccoli",
                    "avocados", "lemons", "limes", "spinach", "bell peppers",
                    
                    # Packaged goods
                    "milk", "eggs", "bread", "butter", "cheese", "yogurt", "cereal",
                    "pasta", "rice", "flour", "sugar", "olive oil", "chicken breast",
                    "ground beef", "salmon", "tuna", "peanut butter", "jelly",
                    
                    # Beverages
                    "orange juice", "apple juice", "soda", "water", "coffee", "tea",
                    "energy drink", "sports drink", "wine", "beer",
                    
                    # Household items
                    "toilet paper", "paper towels", "dish soap", "laundry detergent",
                    "shampoo", "conditioner", "toothpaste", "deodorant", "tissues"
                ],
                CORE_LABELS["QUANTITY"]: [
                    "lb", "lbs", "oz", "kg", "each", "ea", "ct", "count", "pack", "case"
                ]
            },
            
            "retail": {
                CORE_LABELS["PRODUCT_NAME"]: [
                    # Electronics
                    "iphone", "samsung galaxy", "laptop", "tablet", "headphones", "speaker",
                    "charger", "cable", "battery", "mouse", "keyboard", "monitor",
                    "tv", "smart watch", "airpods", "case", "screen protector",
                    
                    # Clothing
                    "shirt", "pants", "jeans", "dress", "jacket", "sweater", "hoodie",
                    "shoes", "sneakers", "boots", "sandals", "socks", "underwear",
                    "hat", "belt", "purse", "wallet", "backpack",
                    
                    # Home goods
                    "pillow", "blanket", "sheets", "towel", "candle", "lamp",
                    "picture frame", "vase", "plant", "storage box", "organizer"
                ],
                CORE_LABELS["PAYMENT_METHOD"]: [
                    "visa", "mastercard", "american express", "discover", "debit",
                    "credit card", "cash", "gift card", "store credit", "apple pay",
                    "google pay", "samsung pay", "paypal", "venmo"
                ]
            },
            
            "pharmacy": {
                CORE_LABELS["PRODUCT_NAME"]: [
                    "prescription", "medication", "pills", "tablets", "capsules",
                    "vitamins", "supplements", "pain reliever", "allergy medicine",
                    "cold medicine", "cough drops", "bandages", "first aid",
                    "thermometer", "blood pressure monitor", "test strips"
                ]
            },
            
            "gas_station": {
                CORE_LABELS["PRODUCT_NAME"]: [
                    "regular gas", "premium gas", "diesel", "unleaded", "gasoline",
                    "car wash", "windshield washer", "motor oil", "energy drink",
                    "coffee", "hot dog", "nachos", "chips", "candy", "gum"
                ]
            }
        }

    async def store_merchant_patterns(
        self, 
        merchant_name: str, 
        patterns: List[MerchantPattern],
        category: Optional[str] = None
    ) -> bool:
        """
        Store merchant-specific patterns in Pinecone.
        
        Args:
            merchant_name: Name of the merchant
            patterns: List of patterns to store
            category: Optional merchant category (restaurant, grocery, etc.)
            
        Returns:
            True if storage successful
        """
        if not self.pinecone_client:
            logger.warning("No Pinecone client configured, patterns not stored")
            return False
        
        try:
            namespace = f"{self.namespace_prefix}_{merchant_name.lower().replace(' ', '_')}"
            
            # Prepare vectors for storage
            vectors_to_upsert = []
            
            for i, pattern in enumerate(patterns):
                # Create vector ID
                vector_id = f"{pattern.label_type}_{pattern.pattern_text}_{i}"
                
                # Create metadata
                metadata = {
                    "merchant_name": merchant_name,
                    "pattern_text": pattern.pattern_text,
                    "label_type": pattern.label_type,
                    "confidence": pattern.confidence,
                    "frequency": pattern.frequency,
                    "last_seen": pattern.last_seen.isoformat(),
                    "category": category or pattern.category,
                    **pattern.metadata
                }
                
                # For now, create a simple embedding based on text
                # In production, this would use actual embeddings
                vector = self._create_pattern_vector(pattern.pattern_text)
                
                vectors_to_upsert.append({
                    "id": vector_id,
                    "values": vector,
                    "metadata": metadata
                })
            
            # Upsert to Pinecone
            self.pinecone_client.upsert(
                vectors=vectors_to_upsert,
                namespace=namespace
            )
            
            self.stats["patterns_stored"] += len(patterns)
            self.stats["merchants_indexed"] = len(set([
                self.stats.get("merchants_indexed", 0), 1
            ]))
            
            logger.info(f"Stored {len(patterns)} patterns for merchant {merchant_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store merchant patterns: {e}")
            return False

    async def retrieve_merchant_patterns(
        self, 
        merchant_name: str, 
        label_types: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """
        Retrieve merchant-specific patterns from Pinecone.
        
        Args:
            merchant_name: Name of the merchant
            label_types: Optional filter for specific label types
            
        Returns:
            Dictionary mapping label types to lists of patterns
        """
        if not self.pinecone_client:
            logger.info("No Pinecone client, using common patterns")
            return self._get_common_patterns_for_merchant(merchant_name, label_types)
        
        try:
            namespace = f"{self.namespace_prefix}_{merchant_name.lower().replace(' ', '_')}"
            
            # Query Pinecone for stored patterns
            # This is a simplified version - production would use semantic search
            patterns = {}
            
            # For now, fall back to common patterns with merchant category detection
            merchant_category = self._detect_merchant_category(merchant_name)
            patterns = self._get_common_patterns_by_category(merchant_category, label_types)
            
            self.stats["patterns_retrieved"] += len(patterns)
            
            logger.info(f"Retrieved {sum(len(v) for v in patterns.values())} patterns for {merchant_name}")
            return patterns
            
        except Exception as e:
            logger.error(f"Failed to retrieve merchant patterns: {e}")
            return self._get_common_patterns_for_merchant(merchant_name, label_types)

    def _detect_merchant_category(self, merchant_name: str) -> str:
        """Detect merchant category based on name."""
        merchant_lower = merchant_name.lower()
        
        # Use loaded category keywords or defaults
        category_keywords = self.merchant_category_keywords
        
        # Check categories in order of specificity
        category_order = ["pharmacy", "gas_station", "restaurant", "grocery", "retail"]
        
        for category in category_order:
            if category in category_keywords:
                keywords = category_keywords[category]
                if any(keyword in merchant_lower for keyword in keywords):
                    return category
        
        return "retail"  # Default category

    def _get_common_patterns_by_category(
        self, 
        category: str, 
        label_types: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """Get common patterns for a merchant category."""
        if category not in self.common_patterns:
            # Check if it's available as a default category
            if category == "retail" and "retail" not in self.common_patterns:
                # Return empty patterns if retail not available
                return {}
            category = "retail" if "retail" in self.common_patterns else list(self.common_patterns.keys())[0]  # Default fallback
        
        patterns = self.common_patterns.get(category, {}).copy()
        
        if label_types:
            # Filter to requested label types
            patterns = {
                label_type: keywords 
                for label_type, keywords in patterns.items()
                if label_type in label_types
            }
        
        return patterns

    def _get_common_patterns_for_merchant(
        self, 
        merchant_name: str, 
        label_types: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """Get common patterns for a specific merchant."""
        category = self._detect_merchant_category(merchant_name)
        return self._get_common_patterns_by_category(category, label_types)

    def _create_pattern_vector(self, text: str) -> List[float]:
        """Create a simple vector representation of pattern text."""
        # This is a placeholder - production would use actual embeddings
        # For now, create a deterministic vector based on text hash
        import hashlib
        
        # Create consistent hash-based vector
        text_hash = hashlib.md5(text.encode()).hexdigest()
        
        # Convert to vector (simplified approach)
        vector = []
        for i in range(0, len(text_hash), 2):
            # Convert hex pairs to floats between -1 and 1
            hex_val = int(text_hash[i:i+2], 16)
            normalized = (hex_val / 255.0) * 2 - 1
            vector.append(normalized)
        
        # Pad or truncate to standard dimension (e.g., 384)
        target_dim = 384
        while len(vector) < target_dim:
            vector.extend(vector[:min(len(vector), target_dim - len(vector))])
        
        return vector[:target_dim]

    async def learn_from_validated_receipt(
        self, 
        merchant_name: str, 
        validated_labels: Dict[str, str],
        receipt_text: List[str]
    ) -> bool:
        """
        Learn new patterns from validated receipt data.
        
        Args:
            merchant_name: Name of the merchant
            validated_labels: Dictionary mapping text to validated labels
            receipt_text: List of text lines from receipt
            
        Returns:
            True if learning successful
        """
        try:
            new_patterns = []
            
            for text, label in validated_labels.items():
                # Only learn from core business labels
                if label in [
                    CORE_LABELS["PRODUCT_NAME"],
                    CORE_LABELS["MERCHANT_NAME"], 
                    CORE_LABELS["TAX"],
                    CORE_LABELS["DISCOUNT"],
                    CORE_LABELS["PAYMENT_METHOD"]
                ]:
                    pattern = MerchantPattern(
                        pattern_text=text.lower(),
                        label_type=label,
                        confidence=0.9,  # High confidence for validated data
                        merchant_name=merchant_name,
                        category=self._detect_merchant_category(merchant_name),
                        frequency=1,
                        metadata={"source": "validated_receipt"}
                    )
                    new_patterns.append(pattern)
            
            if new_patterns:
                await self.store_merchant_patterns(merchant_name, new_patterns)
                logger.info(f"Learned {len(new_patterns)} new patterns from {merchant_name}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to learn from validated receipt: {e}")
            return False

    async def get_enhanced_patterns_for_receipt(
        self, 
        merchant_name: str, 
        receipt_words: List[Dict],
        context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Get enhanced merchant patterns for receipt processing.
        
        This method combines stored patterns with context-aware suggestions
        to provide the most relevant patterns for the current receipt.
        
        Args:
            merchant_name: Name of the merchant
            receipt_words: Words from the receipt
            context: Optional context information
            
        Returns:
            Enhanced pattern dictionary for use with pattern detection
        """
        try:
            # Get base patterns for merchant
            base_patterns = await self.retrieve_merchant_patterns(merchant_name)
            
            # Create enhanced pattern dictionary for pattern detection
            enhanced_patterns = {
                "word_patterns": {},
                "confidence_threshold": 0.7,
                "merchant_name": merchant_name,
                "category": self._detect_merchant_category(merchant_name),
                "context": context or {}
            }
            
            # Convert patterns to word_patterns format
            for label_type, keywords in base_patterns.items():
                for keyword in keywords:
                    enhanced_patterns["word_patterns"][keyword] = label_type
            
            # Add merchant-specific confidence boosting
            if merchant_name.lower() in ["walmart", "target", "kroger"]:
                enhanced_patterns["confidence_threshold"] = 0.8  # Higher confidence for major retailers
            
            logger.info(f"Generated enhanced patterns with {len(enhanced_patterns['word_patterns'])} keywords for {merchant_name}")
            return enhanced_patterns
            
        except Exception as e:
            logger.error(f"Failed to get enhanced patterns: {e}")
            return {
                "word_patterns": {},
                "confidence_threshold": 0.5,
                "merchant_name": merchant_name
            }

    def get_statistics(self) -> Dict[str, Any]:
        """Get merchant pattern database statistics."""
        stats = self.stats.copy()
        
        # Add pattern database summary
        total_patterns = sum(
            len(keywords) 
            for category_patterns in self.common_patterns.values()
            for keywords in category_patterns.values()
        )
        
        stats.update({
            "total_common_patterns": total_patterns,
            "categories_supported": len(self.common_patterns),
            "labels_per_category": {
                category: len(patterns)
                for category, patterns in self.common_patterns.items()
            }
        })
        
        return stats

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "patterns_stored": 0,
            "patterns_retrieved": 0,
            "cache_hits": 0,
            "merchants_indexed": 0,
        }
    
    def reload_patterns(self, patterns_file: Optional[str] = None) -> bool:
        """
        Reload patterns from YAML file.
        
        Args:
            patterns_file: Optional new patterns file path
            
        Returns:
            True if reload successful
        """
        try:
            if patterns_file:
                self.patterns_file = patterns_file
            
            # Reload patterns
            self.common_patterns = self._load_patterns_from_file()
            self.merchant_category_keywords = self._load_merchant_category_keywords()
            
            logger.info(f"Successfully reloaded patterns from {self.patterns_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reload patterns: {e}")
            return False
    
    def export_patterns_to_yaml(self, output_file: str) -> bool:
        """
        Export current patterns to YAML file.
        
        Args:
            output_file: Path to output YAML file
            
        Returns:
            True if export successful
        """
        try:
            # Prepare data for export
            export_data = {}
            
            # Add pattern categories
            for category, patterns in self.common_patterns.items():
                export_data[category] = {}
                for label_type, keywords in patterns.items():
                    # Map back to simple label names for readability
                    label_name = label_type
                    for core_key, core_value in CORE_LABELS.items():
                        if core_value == label_type:
                            label_name = core_key
                            break
                    export_data[category][label_name] = sorted(keywords)
            
            # Add merchant category keywords
            export_data["merchant_categories"] = {
                cat: sorted(keywords) 
                for cat, keywords in self.merchant_category_keywords.items()
            }
            
            # Write to file
            with open(output_file, 'w') as f:
                yaml.dump(export_data, f, default_flow_style=False, sort_keys=True)
            
            logger.info(f"Exported patterns to {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to export patterns: {e}")
            return False
import pytest
from decimal import Decimal
from typing import Dict

from receipt_label.models.line_item import (
    Price,
    Quantity,
    ItemModifier,
    LineItem,
    LineItemAnalysis
)

# Test data fixtures
@pytest.fixture
def sample_price_data() -> Dict:
    return {
        "unit_price": Decimal("10.99"),
        "extended_price": Decimal("21.98")
    }

@pytest.fixture
def sample_quantity_data() -> Dict:
    return {
        "amount": Decimal("2"),
        "unit": "each"
    }

@pytest.fixture
def sample_modifier_data() -> Dict:
    return {
        "type": "discount",
        "description": "Member discount",
        "amount": Decimal("5.00"),
        "percentage": Decimal("10")
    }

@pytest.fixture
def sample_line_item_data(sample_price_data: Dict, sample_quantity_data: Dict) -> Dict:
    return {
        "description": "Test Item",
        "quantity": Quantity(**sample_quantity_data),
        "price": Price(**sample_price_data),
        "confidence": 0.95,
        "line_ids": [1, 2],
        "metadata": {"category": "test"}
    }

@pytest.fixture
def sample_line_item_analysis_data(sample_line_item_data: Dict) -> Dict:
    return {
        "items": [LineItem(**sample_line_item_data)],
        "total_found": 1,
        "subtotal": Decimal("21.98"),
        "tax": Decimal("2.20"),
        "total": Decimal("24.18"),
        "discrepancies": [],
        "confidence": 0.95,
        "metadata": {"store_type": "retail"}
    }

# Price Tests
@pytest.mark.unit
class TestPrice:
    def test_price_creation(self, sample_price_data: Dict):
        """Test creating a Price instance."""
        price = Price(**sample_price_data)
        assert price.unit_price == Decimal("10.99")
        assert price.extended_price == Decimal("21.98")

    def test_price_with_none_values(self):
        """Test creating a Price instance with None values."""
        price = Price()
        assert price.unit_price is None
        assert price.extended_price is None

    def test_price_with_partial_values(self):
        """Test creating a Price instance with partial values."""
        price = Price(unit_price=Decimal("10.99"))
        assert price.unit_price == Decimal("10.99")
        assert price.extended_price is None

# Quantity Tests
@pytest.mark.unit
class TestQuantity:
    def test_quantity_creation(self, sample_quantity_data: Dict):
        """Test creating a Quantity instance."""
        quantity = Quantity(**sample_quantity_data)
        assert quantity.amount == Decimal("2")
        assert quantity.unit == "each"

    def test_quantity_default_unit(self):
        """Test creating a Quantity instance with default unit."""
        quantity = Quantity(amount=Decimal("3"))
        assert quantity.amount == Decimal("3")
        assert quantity.unit == "each"

# ItemModifier Tests
@pytest.mark.unit
class TestItemModifier:
    def test_modifier_creation(self, sample_modifier_data: Dict):
        """Test creating an ItemModifier instance."""
        modifier = ItemModifier(**sample_modifier_data)
        assert modifier.type == "discount"
        assert modifier.description == "Member discount"
        assert modifier.amount == Decimal("5.00")
        assert modifier.percentage == Decimal("10")

    def test_modifier_without_optional_values(self):
        """Test creating an ItemModifier instance without optional values."""
        modifier = ItemModifier(type="void", description="Item voided")
        assert modifier.type == "void"
        assert modifier.description == "Item voided"
        assert modifier.amount is None
        assert modifier.percentage is None

# LineItem Tests
@pytest.mark.unit
class TestLineItem:
    def test_line_item_creation(self, sample_line_item_data: Dict):
        """Test creating a LineItem instance."""
        line_item = LineItem(**sample_line_item_data)
        assert line_item.description == "Test Item"
        assert isinstance(line_item.quantity, Quantity)
        assert isinstance(line_item.price, Price)
        assert line_item.confidence == 0.95
        assert line_item.line_ids == [1, 2]
        assert line_item.metadata == {"category": "test"}

    def test_line_item_default_values(self):
        """Test creating a LineItem instance with default values."""
        line_item = LineItem(description="Test Item")
        assert line_item.description == "Test Item"
        assert line_item.quantity is None
        assert line_item.price is None
        assert line_item.confidence == 0.0
        assert line_item.line_ids == []
        assert line_item.metadata == {}

    def test_line_item_post_init(self):
        """Test LineItem's __post_init__ method."""
        line_item = LineItem(description="Test Item", line_ids=None, metadata=None)
        assert line_item.line_ids == []
        assert line_item.metadata == {}

# LineItemAnalysis Tests
@pytest.mark.unit
class TestLineItemAnalysis:
    def test_analysis_creation(self, sample_line_item_analysis_data: Dict):
        """Test creating a LineItemAnalysis instance."""
        analysis = LineItemAnalysis(**sample_line_item_analysis_data)
        assert len(analysis.items) == 1
        assert analysis.total_found == 1
        assert analysis.subtotal == Decimal("21.98")
        assert analysis.tax == Decimal("2.20")
        assert analysis.total == Decimal("24.18")
        assert analysis.discrepancies == []
        assert analysis.confidence == 0.95
        assert analysis.metadata == {"store_type": "retail"}

    def test_analysis_default_values(self):
        """Test creating a LineItemAnalysis instance with default values."""
        analysis = LineItemAnalysis(items=[])
        assert analysis.items == []
        assert analysis.total_found == 0
        assert analysis.subtotal is None  # Subtotal is None when items is empty
        assert analysis.tax == Decimal("0")  # Tax is always initialized to 0
        assert analysis.total is None  # Total is None when subtotal is None
        assert analysis.discrepancies == []
        assert analysis.confidence == 0.0
        assert analysis.metadata == {}

    def test_analysis_post_init(self):
        """Test LineItemAnalysis's __post_init__ method."""
        analysis = LineItemAnalysis(
            items=[],
            discrepancies=None,
            metadata=None
        )
        assert analysis.discrepancies == []
        assert analysis.metadata == {}
        assert analysis.total_found == 0

    def test_analysis_subtotal_calculation(self, sample_line_item_data: Dict):
        """Test automatic subtotal calculation."""
        items = [LineItem(**sample_line_item_data)]
        analysis = LineItemAnalysis(items=items)
        assert analysis.subtotal == Decimal("21.98")  # From the sample data

    def test_analysis_with_no_prices(self):
        """Test analysis with items that have no prices."""
        items = [LineItem(description="Test Item")]
        analysis = LineItemAnalysis(items=items)
        assert analysis.subtotal == Decimal("0")

    def test_analysis_total_calculation(self):
        """Test automatic total calculation."""
        analysis = LineItemAnalysis(
            items=[],
            subtotal=Decimal("100.00"),
            tax=Decimal("10.00")
        )
        assert analysis.total == Decimal("110.00")
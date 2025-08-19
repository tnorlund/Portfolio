from datetime import datetime
from decimal import Decimal
from typing import Dict

import pytest
from receipt_dynamo.entities.receipt_line_item_analysis import (
    ReceiptLineItemAnalysis)

from receipt_label.models.line_item import (
    ItemModifier,
    LineItem,
    LineItemAnalysis,
    Price,
    Quantity)


# Test data fixtures
@pytest.fixture
def sample_price_data() -> Dict:
    return {"unit_price": Decimal("10.99"), "extended_price": Decimal("21.98")}


@pytest.fixture
def sample_quantity_data() -> Dict:
    return {"amount": Decimal("2"), "unit": "each"}


@pytest.fixture
def sample_modifier_data() -> Dict:
    return {
        "type": "discount",
        "description": "Member discount",
        "amount": Decimal("5.00"),
        "percentage": Decimal("10"),
    }


@pytest.fixture
def sample_line_item_data(
    sample_price_data: Dict, sample_quantity_data: Dict
) -> Dict:
    return {
        "description": "Test Item",
        "quantity": Quantity(**sample_quantity_data),
        "price": Price(**sample_price_data),
        "line_ids": [1, 2],
        "metadata": {"category": "test"},
        "reasoning": "Test reasoning",
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
        "metadata": {"store_type": "retail"},
        "reasoning": "Test analysis reasoning",
    }


@pytest.fixture
def complete_line_item_analysis(
    sample_line_item_analysis_data: Dict) -> LineItemAnalysis:
    """Creates a complete LineItemAnalysis with all required fields for DynamoDB conversion."""
    analysis = LineItemAnalysis(**sample_line_item_analysis_data)
    analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
    analysis.receipt_id = 123
    analysis.version = "1.0.0"
    analysis.timestamp_added = datetime.now().isoformat()
    analysis.word_labels = {(1, 1): {"label": "item_name", "confidence": 0.95}}
    analysis.add_processing_metric("processing_time", 0.5)
    analysis.add_history_event("analysis_completed", {"details": "test"})
    return analysis


@pytest.fixture
def mock_receipt_line_item_analysis(mocker):
    """Creates a mock ReceiptLineItemAnalysis for testing from_dynamo using pytest's mocker."""
    timestamp = datetime.now()

    # Create a mock of ReceiptLineItemAnalysis using pytest's mocker
    mock_analysis = mocker.Mock(spec=ReceiptLineItemAnalysis)

    # Set attributes that will be accessed during from_dynamo
    mock_analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
    mock_analysis.receipt_id = 123
    mock_analysis.timestamp_added = timestamp
    mock_analysis.timestamp_updated = None
    mock_analysis.items = [
        {
            "description": "Test Item",
            "reasoning": "Test reasoning",
            "line_ids": [1, 2],
            "metadata": {"category": "test"},
            "price": {
                "unit_price": Decimal("10.99"),
                "extended_price": Decimal("21.98"),
            },
            "quantity": {"amount": Decimal("2"), "unit": "each"},
        }
    ]
    mock_analysis.reasoning = "Test analysis reasoning"
    mock_analysis.version = "1.0.0"
    mock_analysis.subtotal = Decimal("21.98")
    mock_analysis.tax = Decimal("2.20")
    mock_analysis.total = Decimal("24.18")
    mock_analysis.fees = Decimal("0")
    mock_analysis.discounts = Decimal("0")
    mock_analysis.tips = Decimal("0")
    mock_analysis.total_found = 1
    mock_analysis.discrepancies = []
    mock_analysis.metadata = {
        "processing_metrics": {"processing_time": 0.5},
        "processing_history": [
            {
                "event_type": "analysis_completed",
                "timestamp": timestamp.isoformat(),
                "details": "test",
            }
        ],
        "source_information": {},
    }
    mock_analysis.word_labels = {
        (1, 1): {"label": "item_name", "confidence": 0.95}
    }

    return mock_analysis


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
        assert line_item.line_ids == [1, 2]
        assert line_item.metadata == {"category": "test"}

    def test_line_item_default_values(self):
        """Test creating a LineItem instance with default values."""
        line_item = LineItem(description="Test Item")
        assert line_item.description == "Test Item"
        assert line_item.quantity is None
        assert line_item.price is None
        assert line_item.line_ids == []
        assert line_item.metadata == {}

    def test_line_item_post_init(self):
        """Test LineItem's __post_init__ method."""
        line_item = LineItem(
            description="Test Item", line_ids=None, metadata=None
        )
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
        assert "store_type" in analysis.metadata
        assert analysis.metadata["store_type"] == "retail"

    def test_analysis_default_values(self):
        """Test creating a LineItemAnalysis instance with default values."""
        analysis = LineItemAnalysis(items=[])
        assert analysis.items == []
        assert analysis.total_found == 0
        assert analysis.subtotal is None
        assert analysis.tax == Decimal("0")
        assert analysis.total is None
        assert analysis.discrepancies == []
        assert "processing_metrics" in analysis.metadata
        assert "source_info" in analysis.metadata
        assert "processing_history" in analysis.metadata

    def test_analysis_post_init(self):
        """Test LineItemAnalysis's __post_init__ method."""
        analysis = LineItemAnalysis(
            items=[], discrepancies=None, metadata=None
        )
        assert analysis.discrepancies == []
        assert "processing_metrics" in analysis.metadata
        assert "source_info" in analysis.metadata
        assert "processing_history" in analysis.metadata
        assert len(analysis.metadata["processing_history"]) > 0

    def test_analysis_subtotal_calculation(self, sample_line_item_data: Dict):
        """Test automatic subtotal calculation."""
        items = [LineItem(**sample_line_item_data)]
        analysis = LineItemAnalysis(items=items)
        assert analysis.subtotal == Decimal("21.98")

    def test_analysis_with_no_prices(self):
        """Test analysis with items that have no prices."""
        items = [LineItem(description="Test Item")]
        analysis = LineItemAnalysis(items=items)
        assert analysis.subtotal == Decimal("0")

    def test_analysis_total_calculation(self):
        """Test automatic total calculation."""
        analysis = LineItemAnalysis(
            items=[], subtotal=Decimal("100.00"), tax=Decimal("10.00")
        )
        assert analysis.total == Decimal("110.00")

    @pytest.mark.unit
    class TestDynamoConversion:
        def test_to_dynamo_complete(self, complete_line_item_analysis):
            """Test converting a complete LineItemAnalysis to a ReceiptLineItemAnalysis."""
            dynamo_entity = complete_line_item_analysis.to_dynamo()

            # Verify instance type
            assert isinstance(dynamo_entity, ReceiptLineItemAnalysis)

            # Verify basic fields
            assert (
                dynamo_entity.image_id == complete_line_item_analysis.image_id
            )
            assert (
                dynamo_entity.receipt_id
                == complete_line_item_analysis.receipt_id
            )
            assert dynamo_entity.version == complete_line_item_analysis.version
            assert (
                dynamo_entity.reasoning
                == complete_line_item_analysis.reasoning
            )
            assert (
                dynamo_entity.total_found
                == complete_line_item_analysis.total_found
            )

            # Verify items conversion
            assert len(dynamo_entity.items) == len(
                complete_line_item_analysis.items
            )
            assert dynamo_entity.items[0]["description"] == "Test Item"

            # Verify financial fields
            assert dynamo_entity.subtotal == Decimal("21.98")
            assert dynamo_entity.tax == Decimal("2.20")
            assert dynamo_entity.total == Decimal("24.18")

            # Verify timestamps
            assert dynamo_entity.timestamp_added is not None

            # Verify metadata
            assert "processing_metrics" in dynamo_entity.metadata
            assert "processing_history" in dynamo_entity.metadata

            # Verify word labels
            assert (1, 1) in dynamo_entity.word_labels

        def test_to_dynamo_missing_fields(self):
            """Test that to_dynamo raises ValueError when required fields are missing."""
            analysis = LineItemAnalysis(items=[])

            # Missing both image_id and receipt_id
            with pytest.raises(ValueError, match="receipt_id must be set"):
                analysis.to_dynamo()

            # Missing image_id
            analysis.receipt_id = 123
            with pytest.raises(ValueError, match="image_id must be set"):
                analysis.to_dynamo()

            # Missing receipt_id
            analysis = LineItemAnalysis(items=[])
            analysis.image_id = "550e8400-e29b-41d4-a716-446655440000"
            with pytest.raises(ValueError, match="receipt_id must be set"):
                analysis.to_dynamo()

        def test_from_dynamo(self, mock_receipt_line_item_analysis):
            """Test converting a ReceiptLineItemAnalysis to a LineItemAnalysis."""
            analysis = LineItemAnalysis.from_dynamo(
                mock_receipt_line_item_analysis
            )

            # Verify core fields
            assert (
                analysis.image_id == mock_receipt_line_item_analysis.image_id
            )
            assert (
                analysis.receipt_id
                == mock_receipt_line_item_analysis.receipt_id
            )
            assert analysis.version == mock_receipt_line_item_analysis.version
            assert (
                analysis.reasoning == mock_receipt_line_item_analysis.reasoning
            )
            assert (
                analysis.total_found
                == mock_receipt_line_item_analysis.total_found
            )

            # Verify financial fields
            assert (
                analysis.subtotal == mock_receipt_line_item_analysis.subtotal
            )
            assert analysis.tax == mock_receipt_line_item_analysis.tax
            assert analysis.total == mock_receipt_line_item_analysis.total

            # Verify items conversion
            assert len(analysis.items) == 1
            assert isinstance(analysis.items[0], LineItem)
            assert analysis.items[0].description == "Test Item"
            assert isinstance(analysis.items[0].price, Price)
            assert analysis.items[0].price.unit_price == Decimal("10.99")

            # Verify timestamps converted to ISO format
            assert isinstance(analysis.timestamp_added, str)

            # Verify metadata
            assert "processing_metrics" in analysis.metadata
            assert "processing_history" in analysis.metadata

            # Verify word labels
            assert (1, 1) in analysis.word_labels

        def test_from_dynamo_item_conversion(
            self, mock_receipt_line_item_analysis
        ):
            """Test that items are properly converted from DynamoDB format."""
            analysis = LineItemAnalysis.from_dynamo(
                mock_receipt_line_item_analysis
            )

            # Focus specifically on item conversion
            assert len(analysis.items) == 1
            item = analysis.items[0]

            # Check the LineItem properties
            assert item.description == "Test Item"
            assert item.reasoning == "Test reasoning"
            assert item.line_ids == [1, 2]
            assert item.metadata == {"category": "test"}

            # Check price conversion
            assert isinstance(item.price, Price)
            assert item.price.unit_price == Decimal("10.99")
            assert item.price.extended_price == Decimal("21.98")

            # Check quantity conversion
            assert isinstance(item.quantity, Quantity)
            assert item.quantity.amount == Decimal("2")
            assert item.quantity.unit == "each"

        def test_from_dynamo_metadata_handling(
            self, mock_receipt_line_item_analysis
        ):
            """Test that metadata is properly handled during conversion."""
            analysis = LineItemAnalysis.from_dynamo(
                mock_receipt_line_item_analysis
            )

            # Check that metadata structure is correct
            assert "processing_metrics" in analysis.metadata
            assert "processing_history" in analysis.metadata
            assert "source_information" in analysis.metadata

            # Check specific metadata values
            assert (
                analysis.metadata["processing_metrics"]["processing_time"]
                == 0.5
            )
            assert len(analysis.metadata["processing_history"]) == 1
            assert (
                analysis.metadata["processing_history"][0]["event_type"]
                == "analysis_completed"
            )

        def test_roundtrip_conversion(
            self, mocker, complete_line_item_analysis
        ):
            """Test a complete roundtrip conversion from LineItemAnalysis -> DynamoDB -> LineItemAnalysis."""
            # Convert to DynamoDB
            dynamo_entity = complete_line_item_analysis.to_dynamo()

            # We'll create a shallow copy of the dynamo entity that the from_dynamo method can use
            # This helps us avoid issues with testing a real DynamoDB entity
            mock_entity = mocker.Mock(spec=ReceiptLineItemAnalysis)

            # Copy all attributes from the real entity to our mock
            for attr_name in dir(dynamo_entity):
                # Skip private/special attributes and methods
                if not attr_name.startswith("_") and not callable(
                    getattr(dynamo_entity, attr_name)
                ):
                    setattr(
                        mock_entity,
                        attr_name,
                        getattr(dynamo_entity, attr_name))

            # Convert back to LineItemAnalysis using our mocked entity
            round_trip = LineItemAnalysis.from_dynamo(mock_entity)

            # Verify core fields were preserved
            assert round_trip.image_id == complete_line_item_analysis.image_id
            assert (
                round_trip.receipt_id == complete_line_item_analysis.receipt_id
            )
            assert (
                round_trip.total_found
                == complete_line_item_analysis.total_found
            )
            assert round_trip.subtotal == complete_line_item_analysis.subtotal
            assert round_trip.tax == complete_line_item_analysis.tax
            assert round_trip.total == complete_line_item_analysis.total

            # Verify items were preserved
            assert len(round_trip.items) == len(
                complete_line_item_analysis.items
            )
            assert (
                round_trip.items[0].description
                == complete_line_item_analysis.items[0].description
            )

        def test_roundtrip_line_item_preserved(
            self, mocker, complete_line_item_analysis
        ):
            """Test that line item details are preserved in a roundtrip conversion."""
            # Convert to DynamoDB
            dynamo_entity = complete_line_item_analysis.to_dynamo()

            # Create mock for testing roundtrip
            mock_entity = mocker.Mock(spec=ReceiptLineItemAnalysis)
            for attr_name in dir(dynamo_entity):
                if not attr_name.startswith("_") and not callable(
                    getattr(dynamo_entity, attr_name)
                ):
                    setattr(
                        mock_entity,
                        attr_name,
                        getattr(dynamo_entity, attr_name))

            # Convert back
            round_trip = LineItemAnalysis.from_dynamo(mock_entity)

            # Compare line items in detail
            original_item = complete_line_item_analysis.items[0]
            round_trip_item = round_trip.items[0]

            # Check line item fields
            assert round_trip_item.line_ids == original_item.line_ids
            assert round_trip_item.description == original_item.description
            assert round_trip_item.reasoning == original_item.reasoning

            # Check price
            assert (
                round_trip_item.price.unit_price
                == original_item.price.unit_price
            )
            assert (
                round_trip_item.price.extended_price
                == original_item.price.extended_price
            )

            # Check quantity
            assert (
                round_trip_item.quantity.amount
                == original_item.quantity.amount
            )
            assert round_trip_item.quantity.unit == original_item.quantity.unit

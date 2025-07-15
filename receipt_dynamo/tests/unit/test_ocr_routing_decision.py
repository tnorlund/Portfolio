from datetime import datetime

import pytest

from receipt_dynamo import OCRRoutingDecision, item_to_ocr_routing_decision
from receipt_dynamo.constants import OCRStatus


@pytest.fixture
def example_ocr_routing_decision():
    return OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 13, 0, 0),
        receipt_count=3,
        status=OCRStatus.COMPLETED,
    )


@pytest.mark.unit
def test_ocr_routing_decision_init_valid(example_ocr_routing_decision):
    """Test the OCRRoutingDecision constructor"""
    assert (
        example_ocr_routing_decision.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_ocr_routing_decision.job_id
        == "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    )
    assert example_ocr_routing_decision.s3_bucket == "test-bucket"
    assert example_ocr_routing_decision.s3_key == "test-key/image.jpg"
    assert example_ocr_routing_decision.created_at == datetime(
        2024, 1, 1, 12, 0, 0
    )
    assert example_ocr_routing_decision.updated_at == datetime(
        2024, 1, 1, 13, 0, 0
    )
    assert example_ocr_routing_decision.receipt_count == 3
    assert example_ocr_routing_decision.status == "COMPLETED"


@pytest.mark.unit
def test_ocr_routing_decision_init_with_string_datetime():
    """Test the OCRRoutingDecision constructor with string datetime"""
    decision = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at="2024-01-01T12:00:00",
        updated_at="2024-01-01T13:00:00",
        receipt_count=3,
        status="PENDING",
    )
    assert isinstance(decision.created_at, datetime)
    assert isinstance(decision.updated_at, datetime)
    assert decision.created_at == datetime(2024, 1, 1, 12, 0, 0)
    assert decision.updated_at == datetime(2024, 1, 1, 13, 0, 0)


@pytest.mark.unit
def test_ocr_routing_decision_init_with_none_updated_at():
    """Test the OCRRoutingDecision constructor with None updated_at"""
    decision = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=None,
        receipt_count=3,
        status=OCRStatus.PENDING,
    )
    assert decision.updated_at is None


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_image_id():
    """Test the OCRRoutingDecision constructor with bad image_id"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        OCRRoutingDecision(
            image_id=1,
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket="test-bucket",
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status=OCRStatus.PENDING,
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        OCRRoutingDecision(
            image_id="not-a-uuid",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket="test-bucket",
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status=OCRStatus.PENDING,
        )


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_job_id():
    """Test the OCRRoutingDecision constructor with bad job_id"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        OCRRoutingDecision(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id=1,
            s3_bucket="test-bucket",
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status=OCRStatus.PENDING,
        )


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_s3_bucket():
    """Test the OCRRoutingDecision constructor with bad s3_bucket"""
    with pytest.raises(ValueError, match="s3_bucket must be a string"):
        OCRRoutingDecision(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket=123,
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status=OCRStatus.PENDING,
        )


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_s3_key():
    """Test the OCRRoutingDecision constructor with bad s3_key"""
    with pytest.raises(ValueError, match="s3_key must be a string"):
        OCRRoutingDecision(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket="test-bucket",
            s3_key=123,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status=OCRStatus.PENDING,
        )


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_receipt_count():
    """Test the OCRRoutingDecision constructor with bad receipt_count"""
    with pytest.raises(ValueError, match="receipt_count must be an integer"):
        OCRRoutingDecision(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket="test-bucket",
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count="3",
            status=OCRStatus.PENDING,
        )


@pytest.mark.unit
def test_ocr_routing_decision_init_invalid_status():
    """Test the OCRRoutingDecision constructor with bad status"""
    with pytest.raises(ValueError, match="must be one of"):
        OCRRoutingDecision(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            s3_bucket="test-bucket",
            s3_key="test-key/image.jpg",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=None,
            receipt_count=3,
            status="INVALID_STATUS",
        )


@pytest.mark.unit
def test_ocr_routing_decision_key(example_ocr_routing_decision):
    """Test the OCRRoutingDecision key property"""
    expected_key = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "ROUTING#4f52804b-2fad-4e00-92c8-b593da3a8ed4"},
    }
    assert example_ocr_routing_decision.key == expected_key


@pytest.mark.unit
def test_ocr_routing_decision_gsi1_key(example_ocr_routing_decision):
    """Test the OCRRoutingDecision gsi1_key method"""
    expected_gsi1_key = {
        "GSI1PK": {"S": "OCR_ROUTING_DECISION_STATUS#COMPLETED"},
        "GSI1SK": {"S": "ROUTING#4f52804b-2fad-4e00-92c8-b593da3a8ed4"},
    }
    assert example_ocr_routing_decision.gsi1_key() == expected_gsi1_key


@pytest.mark.unit
def test_ocr_routing_decision_to_item(example_ocr_routing_decision):
    """Test the OCRRoutingDecision to_item method"""
    item = example_ocr_routing_decision.to_item()

    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "ROUTING#4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    assert item["TYPE"]["S"] == "OCR_ROUTING_DECISION"
    assert item["s3_bucket"]["S"] == "test-bucket"
    assert item["s3_key"]["S"] == "test-key/image.jpg"
    assert item["created_at"]["S"] == "2024-01-01T12:00:00"
    assert item["updated_at"]["S"] == "2024-01-01T13:00:00"
    assert item["receipt_count"]["N"] == "3"
    assert item["status"]["S"] == "COMPLETED"
    assert item["GSI1PK"]["S"] == "OCR_ROUTING_DECISION_STATUS#COMPLETED"
    assert (
        item["GSI1SK"]["S"] == "ROUTING#4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    )


@pytest.mark.unit
def test_ocr_routing_decision_to_item_with_none_updated_at():
    """Test the OCRRoutingDecision to_item method with None updated_at"""
    decision = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=None,
        receipt_count=3,
        status=OCRStatus.PENDING,
    )
    item = decision.to_item()
    assert item["updated_at"]["NULL"] is True


@pytest.mark.unit
def test_ocr_routing_decision_repr(example_ocr_routing_decision):
    """Test the OCRRoutingDecision __repr__ method"""
    repr_str = repr(example_ocr_routing_decision)
    assert "OCRRoutingDecision" in repr_str
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "job_id='4f52804b-2fad-4e00-92c8-b593da3a8ed4'" in repr_str
    assert "s3_bucket='test-bucket'" in repr_str
    assert "s3_key='test-key/image.jpg'" in repr_str
    assert "receipt_count=3" in repr_str
    assert "status='COMPLETED'" in repr_str


@pytest.mark.unit
def test_ocr_routing_decision_hash(example_ocr_routing_decision):
    """Test the OCRRoutingDecision __hash__ method"""
    # Create two identical OCRRoutingDecision objects
    decision1 = example_ocr_routing_decision
    decision2 = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 13, 0, 0),
        receipt_count=3,
        status=OCRStatus.COMPLETED,
    )

    # They should have the same hash
    assert hash(decision1) == hash(decision2)

    # Different object should have different hash
    decision3 = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 13, 0, 0),
        receipt_count=4,  # Different receipt count
        status=OCRStatus.COMPLETED,
    )
    assert hash(decision1) != hash(decision3)


@pytest.mark.unit
def test_ocr_routing_decision_equality(example_ocr_routing_decision):
    """Test the OCRRoutingDecision equality"""
    decision1 = example_ocr_routing_decision
    decision2 = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 13, 0, 0),
        receipt_count=3,
        status=OCRStatus.COMPLETED,
    )

    assert decision1 == decision2


@pytest.mark.unit
def test_item_to_ocr_routing_decision(example_ocr_routing_decision):
    """Test the item_to_ocr_routing_decision function"""
    item = example_ocr_routing_decision.to_item()
    reconstructed = item_to_ocr_routing_decision(item)

    assert reconstructed.image_id == example_ocr_routing_decision.image_id
    assert reconstructed.job_id == example_ocr_routing_decision.job_id
    assert reconstructed.s3_bucket == example_ocr_routing_decision.s3_bucket
    assert reconstructed.s3_key == example_ocr_routing_decision.s3_key
    assert reconstructed.created_at == example_ocr_routing_decision.created_at
    assert reconstructed.updated_at == example_ocr_routing_decision.updated_at
    assert (
        reconstructed.receipt_count
        == example_ocr_routing_decision.receipt_count
    )
    assert reconstructed.status == example_ocr_routing_decision.status


@pytest.mark.unit
def test_item_to_ocr_routing_decision_with_none_updated_at():
    """Test the item_to_ocr_routing_decision function with None updated_at"""
    decision = OCRRoutingDecision(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        s3_bucket="test-bucket",
        s3_key="test-key/image.jpg",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=None,
        receipt_count=3,
        status=OCRStatus.PENDING,
    )
    item = decision.to_item()
    reconstructed = item_to_ocr_routing_decision(item)

    assert reconstructed.updated_at is None


@pytest.mark.unit
def test_item_to_ocr_routing_decision_invalid_item():
    """Test the item_to_ocr_routing_decision function with invalid item"""
    # Missing required keys
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "ROUTING#4f52804b-2fad-4e00-92c8-b593da3a8ed4"},
        "TYPE": {"S": "OCR_ROUTING_DECISION"},
        # Missing other required fields
    }

    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_ocr_routing_decision(item)

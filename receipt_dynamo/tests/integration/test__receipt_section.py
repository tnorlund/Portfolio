from datetime import datetime
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import SectionType
from receipt_dynamo.entities.receipt_section import ReceiptSection


@pytest.fixture
def sample_receipt_section():
    """Returns a valid ReceiptSection object with placeholder data."""
    # Use a fixed timestamp without microseconds for consistency
    now = datetime.utcnow().replace(microsecond=0)
    return ReceiptSection(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        section_type=SectionType.HEADER,
        line_ids=[1, 2, 3],
        created_at=now,
    )


@pytest.mark.integration
def test_add_receipt_section(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_section: ReceiptSection,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addReceiptSection(sample_receipt_section)

    # Assert
    retrieved = client.getReceiptSection(
        sample_receipt_section.receipt_id,
        sample_receipt_section.image_id,
        sample_receipt_section.section_type,
    )
    assert retrieved == sample_receipt_section


@pytest.mark.integration
def test_add_receipt_section_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_section: ReceiptSection,
):
    client = DynamoClient(dynamodb_table)
    client.addReceiptSection(sample_receipt_section)
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptSection(sample_receipt_section)


@pytest.mark.integration
def test_update_receipt_section(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_section: ReceiptSection,
):
    client = DynamoClient(dynamodb_table)
    # Start with HEADER
    sample_receipt_section.section_type = SectionType.HEADER
    client.addReceiptSection(sample_receipt_section)

    # Delete the old section before updating with new section type
    client.deleteReceiptSection(
        sample_receipt_section.receipt_id,
        sample_receipt_section.image_id,
        sample_receipt_section.section_type,
    )

    # Modify section_type to FOOTER and add as new section
    sample_receipt_section.section_type = SectionType.FOOTER
    client.addReceiptSection(sample_receipt_section)

    # Verify the section was updated
    retrieved = client.getReceiptSection(
        sample_receipt_section.receipt_id,
        sample_receipt_section.image_id,
        sample_receipt_section.section_type,
    )
    assert retrieved.section_type == SectionType.FOOTER


@pytest.mark.integration
def test_delete_receipt_section(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_section: ReceiptSection,
):
    client = DynamoClient(dynamodb_table)
    client.addReceiptSection(sample_receipt_section)

    client.deleteReceiptSection(
        sample_receipt_section.receipt_id,
        sample_receipt_section.image_id,
        sample_receipt_section.section_type,
    )

    with pytest.raises(ValueError, match="not found"):
        client.getReceiptSection(
            sample_receipt_section.receipt_id,
            sample_receipt_section.image_id,
            sample_receipt_section.section_type,
        )


@pytest.mark.integration
def test_receipt_section_list(dynamodb_table: Literal["MyMockedTable"]):
    client = DynamoClient(dynamodb_table)
    now = datetime.utcnow().replace(microsecond=0)

    # Create multiple sections for the same receipt/image but with different section types
    sections = [
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=section_type,
            line_ids=[i, i + 1],
            created_at=now,
        )
        for i, section_type in enumerate(
            [SectionType.HEADER, SectionType.ITEMS_VALUE, SectionType.FOOTER],
            1,
        )
    ]
    for sec in sections:
        client.addReceiptSection(sec)

    # Act
    returned_sections, _ = client.listReceiptSections()

    # Assert
    for sec in sections:
        assert sec in returned_sections

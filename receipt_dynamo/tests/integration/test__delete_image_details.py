"""Integration tests for delete_image_details in DynamoDB."""

import datetime
from typing import Literal

import pytest

from receipt_dynamo import (
    DynamoClient,
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
BB = {"x": 0, "y": 0, "width": 100, "height": 100}
POINT_TL = {"x": 0, "y": 0}
POINT_TR = {"x": 100, "y": 0}
POINT_BL = {"x": 0, "y": 100}
POINT_BR = {"x": 100, "y": 100}


def _make_image(image_id: str = IMAGE_ID) -> Image:
    return Image(
        image_id=image_id,
        width=100,
        height=100,
        timestamp_added=datetime.datetime.now(datetime.timezone.utc),
        raw_s3_bucket="test-bucket",
        raw_s3_key="test-key",
        sha256="test-sha256",
        cdn_s3_bucket="test-cdn-bucket",
        cdn_s3_key="test-cdn-key",
    )


def _make_line(image_id: str = IMAGE_ID, line_id: int = 1) -> Line:
    return Line(
        image_id=image_id,
        line_id=line_id,
        text="Hello",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_word(
    image_id: str = IMAGE_ID, line_id: int = 1, word_id: int = 1
) -> Word:
    return Word(
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text="Hello",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_letter(
    image_id: str = IMAGE_ID,
    line_id: int = 1,
    word_id: int = 1,
    letter_id: int = 1,
) -> Letter:
    return Letter(
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        letter_id=letter_id,
        text="H",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_receipt(image_id: str = IMAGE_ID, receipt_id: int = 1) -> Receipt:
    return Receipt(
        image_id=image_id,
        receipt_id=receipt_id,
        width=100,
        height=100,
        timestamp_added=datetime.datetime.now(datetime.timezone.utc),
        raw_s3_bucket="test-bucket",
        raw_s3_key="test-key",
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
    )


def _make_receipt_line(
    image_id: str = IMAGE_ID, receipt_id: int = 1, line_id: int = 1
) -> ReceiptLine:
    return ReceiptLine(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        text="Hello",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_receipt_word(
    image_id: str = IMAGE_ID,
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
) -> ReceiptWord:
    return ReceiptWord(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text="Hello",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_receipt_letter(
    image_id: str = IMAGE_ID,
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
    letter_id: int = 1,
) -> ReceiptLetter:
    return ReceiptLetter(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        letter_id=letter_id,
        text="H",
        bounding_box=BB,
        top_left=POINT_TL,
        top_right=POINT_TR,
        bottom_left=POINT_BL,
        bottom_right=POINT_BR,
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


def _make_receipt_word_label(
    image_id: str = IMAGE_ID,
    receipt_id: int = 1,
    line_id: int = 1,
    word_id: int = 1,
) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label="PRODUCT_NAME",
        reasoning="test label",
        timestamp_added=datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        validation_status="VALID",
    )


def _make_receipt_place(
    image_id: str = IMAGE_ID, receipt_id: int = 1
) -> ReceiptPlace:
    return ReceiptPlace(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id="ChIJtest123",
        merchant_name="Test Store",
    )


@pytest.mark.integration
class TestDeleteImageDetails:
    """Tests for DynamoClient.delete_image_details()."""

    def test_deletes_all_entity_types(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Deletes image plus all children across multiple entity types."""
        client = DynamoClient(dynamodb_table)

        # Create a full hierarchy
        image = _make_image()
        line = _make_line()
        word = _make_word()
        letter = _make_letter()
        receipt = _make_receipt()
        receipt_line = _make_receipt_line()
        receipt_word = _make_receipt_word()
        receipt_letter = _make_receipt_letter()
        receipt_word_label = _make_receipt_word_label()
        receipt_place = _make_receipt_place()

        client.add_image(image)
        client.add_lines([line])
        client.add_words([word])
        client.add_letters([letter])
        client.add_receipts([receipt])
        client.add_receipt_lines([receipt_line])
        client.add_receipt_words([receipt_word])
        client.add_receipt_letters([receipt_letter])
        client.add_receipt_word_label(receipt_word_label)
        client.add_receipt_place(receipt_place)

        # Act
        counts = client.delete_image_details(IMAGE_ID)

        # Assert — all entity types represented
        assert counts["IMAGE"] == 1
        assert counts["LINE"] == 1
        assert counts["WORD"] == 1
        assert counts["LETTER"] == 1
        assert counts["RECEIPT"] == 1
        assert counts["RECEIPT_LINE"] == 1
        assert counts["RECEIPT_WORD"] == 1
        assert counts["RECEIPT_LETTER"] == 1
        assert counts["RECEIPT_WORD_LABEL"] == 1
        assert counts["RECEIPT_PLACE"] == 1
        assert sum(counts.values()) == 10

        # Verify nothing remains
        details = client.get_image_details(IMAGE_ID)
        assert len(details.images) == 0
        assert len(details.lines) == 0
        assert len(details.words) == 0
        assert len(details.letters) == 0
        assert len(details.receipts) == 0
        assert len(details.receipt_lines) == 0
        assert len(details.receipt_words) == 0
        assert len(details.receipt_letters) == 0
        assert len(details.receipt_word_labels) == 0
        assert len(details.receipt_places) == 0

    def test_returns_empty_dict_for_nonexistent_image(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Returns empty dict when no items exist for the given image_id."""
        client = DynamoClient(dynamodb_table)
        counts = client.delete_image_details(
            "d3f52804-2fad-4e00-92c8-b593da3a8ed3"
        )
        assert counts == {}

    def test_does_not_affect_other_images(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Deleting one image leaves another image's data intact."""
        client = DynamoClient(dynamodb_table)

        other_id = "e4f52804-2fad-4e00-92c8-b593da3a8ed3"

        # Create data for two images
        client.add_image(_make_image(IMAGE_ID))
        client.add_lines([_make_line(IMAGE_ID)])
        client.add_image(_make_image(other_id))
        client.add_lines([_make_line(other_id)])

        # Delete only the first
        counts = client.delete_image_details(IMAGE_ID)
        assert counts["IMAGE"] == 1
        assert counts["LINE"] == 1

        # The other image is untouched
        other_details = client.get_image_details(other_id)
        assert len(other_details.images) == 1
        assert len(other_details.lines) == 1

    def test_handles_many_items(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Handles more than 25 items (batch write chunk size)."""
        client = DynamoClient(dynamodb_table)

        client.add_image(_make_image())
        # 30 lines -> exceeds batch size of 25
        lines = [_make_line(line_id=i) for i in range(1, 31)]
        client.add_lines(lines)

        counts = client.delete_image_details(IMAGE_ID)
        assert counts["IMAGE"] == 1
        assert counts["LINE"] == 30
        assert sum(counts.values()) == 31

        # Verify empty
        details = client.get_image_details(IMAGE_ID)
        assert len(details.images) == 0
        assert len(details.lines) == 0

    def test_idempotent_on_second_call(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Second call on the same image returns empty dict."""
        client = DynamoClient(dynamodb_table)

        client.add_image(_make_image())
        client.add_lines([_make_line()])

        first = client.delete_image_details(IMAGE_ID)
        assert sum(first.values()) == 2

        second = client.delete_image_details(IMAGE_ID)
        assert second == {}

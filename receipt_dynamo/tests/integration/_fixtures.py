import json
import os

import pytest

from receipt_dynamo import (
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
)

CURRENT_DIR = os.path.dirname(__file__)


@pytest.fixture
def sample_gpt_receipt_1():
    """
    Provides the Image, Lines, Words, Letters, Receipt, and ReceiptWords for testing parsing a GPT response.
    """
    json_path = os.path.join(
        CURRENT_DIR, "JSON", "5119873a-5401-4dd1-9669-878dff8b5bd5.json"
    )
    with open(json_path, "r") as f:
        data = json.load(f)
    return (
        [Image(**image_dict) for image_dict in data["images"]],
        [Line(**line_dict) for line_dict in data["lines"]],
        [Word(**word_dict) for word_dict in data["words"]],
        [Letter(**letter_dict) for letter_dict in data["letters"]],
        [Receipt(**receipt_dict) for receipt_dict in data["receipts"]],
        [ReceiptLine(**line_dict) for line_dict in data["receipt_lines"]],
        [ReceiptWord(**word_dict) for word_dict in data["receipt_words"]],
        [
            ReceiptLetter(**letter_dict)
            for letter_dict in data["receipt_letters"]
        ],
        {
            "store_name": {
                "value": "VONS",
                "word_centroids": [
                    {"x": 0.5512062150516704, "y": 0.9408981712656039}
                ],
            },
            "date": {
                "value": "03/19/24",
                "word_centroids": [
                    {"x": 0.5496056665785586, "y": 0.48408131950268973}
                ],
            },
            "time": {
                "value": "13:29",
                "word_centroids": [
                    {"x": 0.7058637366289461, "y": 0.48047478008137867}
                ],
            },
            "phone_number": {
                "value": "877-276-9637",
                "word_centroids": [
                    {"x": 0.261267280794621, "y": 0.01903172766152822}
                ],
            },
            "total_amount": {
                "value": 3.6,
                "word_centroids": [
                    {"x": 0.7717564238292309, "y": 0.2714470647540924}
                ],
            },
            "items": [
                {
                    "item_name": {
                        "value": "PURE LIFE WATER",
                        "word_centroids": [
                            {
                                "x": 0.26749815892255185,
                                "y": 0.6302302906437088,
                            },
                            {
                                "x": 0.35508028162160254,
                                "y": 0.6289789569648202,
                            },
                            {
                                "x": 0.45584469338370615,
                                "y": 0.6273388948665621,
                            },
                        ],
                    },
                    "price": {
                        "value": 3.6,
                        "word_centroids": [
                            {"x": 0.6804826678128096, "y": 0.40209401707470505}
                        ],
                    },
                }
            ],
            "taxes": {"value": 0.0, "word_centroids": []},
            "address": {
                "value": "2725 Agoura Road WESTLAKE CA 91360",
                "word_centroids": [
                    {"x": 0.6391207477919513, "y": 0.8665352414902137},
                    {"x": 0.5349336755910249, "y": 0.8452982975343265},
                    {"x": 0.6559380329343767, "y": 0.8428092825914741},
                    {"x": 0.4310173820936261, "y": 0.8247879395491544},
                    {"x": 0.5604149374524438, "y": 0.8222577449404934},
                    {"x": 0.6540044868788929, "y": 0.8202571242234046},
                ],
            },
        },
    )


@pytest.fixture
def sample_receipt_details():
    """
    Provides a sample receipt with its associated words and word labels for testing.
    """
    receipt = Receipt(
        image_id="test_image",
        receipt_id=1,
        confidence=0.95,
        store_name="Test Store",
        date="2024-03-19",
        time="13:29",
        total_amount=3.6,
        taxes=0.0,
        address="123 Test St, Test City, CA 12345",
        phone_number="123-456-7890",
        items=[
            {
                "item_name": "Test Item",
                "price": 3.6,
                "quantity": 1,
                "total": 3.6,
            }
        ],
    )

    receipt_words = [
        ReceiptWord(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="Test",
            confidence=0.95,
            x1=0.1,
            y1=0.1,
            x2=0.2,
            y2=0.2,
        ),
        ReceiptWord(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=2,
            text="Store",
            confidence=0.95,
            x1=0.3,
            y1=0.1,
            x2=0.4,
            y2=0.2,
        ),
    ]

    word_labels = [
        ReceiptWordLabel(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="store_name",
            confidence=0.95,
        ),
        ReceiptWordLabel(
            image_id="test_image",
            receipt_id=1,
            line_id=1,
            word_id=2,
            label="store_name",
            confidence=0.95,
        ),
    ]

    return {
        "receipt": receipt,
        "words": receipt_words,
        "word_labels": word_labels,
    }

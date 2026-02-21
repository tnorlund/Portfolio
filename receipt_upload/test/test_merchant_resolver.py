"""
Integration tests for MerchantResolver.

Tests the two-tier merchant resolution strategy:
- Tier 1: Fast metadata filtering on phone/address in ChromaDB
- Tier 2: Place ID Finder agent fallback for Google Places API

All external services (ChromaDB, DynamoDB, Places API) are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

from receipt_upload.merchant_resolution import (
    MerchantResolver,
    MerchantResult,
    merchant_name_matches_receipt,
    tokenize_text,
)


class TestMerchantResolverTier1Phone:
    """Test Tier 1 phone-based merchant resolution."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        client = MagicMock()
        return client

    @pytest.fixture
    def mock_lines_client(self):
        """Create mock ChromaClient for lines collection."""
        client = MagicMock()
        return client

    @pytest.fixture
    def resolver(self, mock_dynamo_client):
        """Create MerchantResolver with mock clients."""
        return MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

    def test_phone_match_returns_merchant_result(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """Test successful phone-based merchant resolution."""
        # Set up words with phone extracted_data
        words = [
            MagicMock(
                spec=ReceiptWord,
                text="(555) 123-4567",
                line_id=1,
                extracted_data={"type": "phone", "value": "5551234567"},
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Matching Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock ChromaDB query to return matching receipt
        # Distance of 0.1 = similarity of 0.95 (1 - 0.1/2)
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-image",
                        "receipt_id": 99,
                        "merchant_name": "Matching Store",
                        "normalized_phone_10": "5551234567",
                    }
                ]
            ],
            "distances": [[0.1]],
        }

        # Mock DynamoDB to return place_id and merchant_name
        mock_place = MagicMock()
        mock_place.place_id = "ChIJ_test_place_id"
        mock_place.merchant_name = "Matching Store"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        # Provide pre-cached line embeddings to avoid OpenAI API calls
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="current-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        assert result.place_id == "ChIJ_test_place_id"
        assert result.resolution_tier == "chroma_phone"
        # With phone match boost: 0.95 + 0.20 = 1.0 (capped)
        assert result.confidence >= 0.95
        assert result.source_image_id == "other-image"
        assert result.source_receipt_id == 99

    def test_phone_match_skips_current_receipt(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """Test that phone match skips the current receipt."""
        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=1,
                extracted_data={"type": "phone", "value": "5551234567"},
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock ChromaDB to return only the current receipt
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "current-image",
                        "receipt_id": 1,
                        "merchant_name": "My Store",
                    }
                ]
            ],
            "distances": [[0.1]],
        }

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="current-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should not find a match (current receipt skipped)
        assert result.place_id is None

    def test_no_phone_extracted_proceeds_to_address(
        self, resolver, mock_lines_client
    ):
        """Test that missing phone proceeds to address tier."""
        words = [MagicMock(spec=ReceiptWord, line_id=1, extracted_data={})]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # No matching results from ChromaDB
        mock_lines_client.query.return_value = {
            "metadatas": [[]],
            "distances": [[]],
        }

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="test-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should not find a match (no phone or address)
        assert result.place_id is None


class TestMerchantResolverTier1Address:
    """Test Tier 1 address-based merchant resolution."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        client = MagicMock()
        return client

    @pytest.fixture
    def mock_lines_client(self):
        """Create mock ChromaClient for lines collection."""
        client = MagicMock()
        return client

    @pytest.fixture
    def resolver(self, mock_dynamo_client):
        """Create MerchantResolver with mock clients."""
        return MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

    def test_address_match_returns_merchant_result(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """Test successful address-based merchant resolution."""
        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=1,
                extracted_data={
                    "type": "address",
                    "value": "123 Main Street, New York, NY 10001",
                },
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store Name")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock ChromaDB query to return matching receipt
        # Distance of 0.4 = similarity of 0.80 (1 - 0.4/2)
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-image",
                        "receipt_id": 88,
                        "merchant_name": "Address Match Store",
                        "normalized_full_address": "123 MAIN ST NEW YORK NY 10001",
                    }
                ]
            ],
            "distances": [[0.4]],
        }

        # Mock DynamoDB to return place_id and merchant_name
        mock_place = MagicMock()
        mock_place.place_id = "ChIJ_address_place_id"
        mock_place.merchant_name = "Address Match Store"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="current-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        assert result.place_id == "ChIJ_address_place_id"
        assert result.resolution_tier == "chroma_address"
        # Similarity 0.80 + address match boost 0.15 = 0.95
        assert result.confidence >= 0.80
        assert result.source_image_id == "other-image"


class TestMerchantResolverTier2PlaceIdFinder:
    """Test Tier 2 Place ID Finder fallback."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        return MagicMock()

    @pytest.fixture
    def mock_places_client(self):
        """Create mock PlacesClient."""
        return MagicMock()

    @pytest.fixture
    def mock_lines_client(self):
        """Create mock ChromaClient."""
        client = MagicMock()
        # Return empty results for Tier 1 (no matches)
        client.query.return_value = {"metadatas": [[]], "distances": [[]]}
        return client

    def test_tier2_fallback_when_tier1_fails(
        self,
        mock_dynamo_client,
        mock_places_client,
        mock_lines_client,
    ):
        """Test that Tier 2 is invoked when Tier 1 fails."""
        resolver = MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=mock_places_client,
        )

        words = [MagicMock(spec=ReceiptWord, line_id=1, extracted_data={})]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Coffee Shop")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock _run_place_id_finder method directly to avoid circular import
        tier2_result = MerchantResult(
            place_id="ChIJ_tier2_place_id",
            merchant_name="Coffee Shop Inc",
            address="456 Oak Ave",
            phone="5559876543",
            confidence=0.85,
            resolution_tier="place_id_finder",
        )

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        with patch.object(
            resolver, "_run_place_id_finder", return_value=tier2_result
        ):
            result = resolver.resolve(
                lines_client=mock_lines_client,
                lines=lines,
                words=words,
                image_id="test-image",
                receipt_id=1,
                line_embeddings=line_embeddings,
            )

        assert result.place_id == "ChIJ_tier2_place_id"
        assert result.merchant_name == "Coffee Shop Inc"
        assert result.resolution_tier == "place_id_finder"
        assert result.confidence == 0.85

    def test_tier2_returns_empty_when_no_match(
        self,
        mock_dynamo_client,
        mock_places_client,
        mock_lines_client,
    ):
        """Test Tier 2 returns empty result when no match found."""
        resolver = MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=mock_places_client,
        )

        words = [MagicMock(spec=ReceiptWord, line_id=1, extracted_data={})]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Unknown")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        # Mock _run_place_id_finder to return empty result
        with patch.object(
            resolver, "_run_place_id_finder", return_value=MerchantResult()
        ):
            result = resolver.resolve(
                lines_client=mock_lines_client,
                lines=lines,
                words=words,
                image_id="test-image",
                receipt_id=1,
                line_embeddings=line_embeddings,
            )

        assert result.place_id is None
        assert result.resolution_tier is None


class TestMerchantResolverHelpers:
    """Test helper methods for extraction."""

    @pytest.fixture
    def resolver(self):
        """Create resolver with mock clients."""
        return MerchantResolver(
            dynamo_client=MagicMock(),
            places_client=None,
        )

    def test_extract_phone_from_words(self, resolver):
        """Test phone extraction from words."""
        words = [
            MagicMock(extracted_data={"type": "name", "value": "Store"}),
            MagicMock(
                extracted_data={"type": "phone", "value": "555-123-4567"},
                text="555-123-4567",
            ),
        ]

        phone = resolver._extract_phone(words)
        assert phone == "5551234567"

    def test_extract_phone_returns_none_when_missing(self, resolver):
        """Test phone extraction returns None when no phone."""
        words = [
            MagicMock(extracted_data={"type": "address", "value": "123 Main"})
        ]

        phone = resolver._extract_phone(words)
        assert phone is None

    def test_extract_address_from_words(self, resolver):
        """Test address extraction from words."""
        words = [
            MagicMock(
                extracted_data={
                    "type": "address",
                    "value": "123 Main Street",
                }
            ),
            MagicMock(
                extracted_data={
                    "type": "address",
                    "value": "New York, NY 10001",
                }
            ),
        ]

        address = resolver._extract_address(words)
        assert "MAIN" in address.upper()
        assert "NEW YORK" in address.upper()

    def test_extract_merchant_name_from_first_line(self, resolver):
        """Test merchant name extraction from first line."""
        mock_line1 = MagicMock(
            spec=ReceiptLine, line_id=2, text="123 Address St"
        )
        mock_line1.calculate_centroid.return_value = (0.5, 0.3)
        mock_line2 = MagicMock(spec=ReceiptLine, line_id=1, text="Store Name")
        mock_line2.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line1, mock_line2]

        name = resolver._extract_merchant_name(lines)
        assert name == "Store Name"

    def test_extract_merchant_name_returns_first_line_text(self, resolver):
        """Test that merchant name is extracted from the first line."""
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="123 Main St")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        name = resolver._extract_merchant_name(lines)
        # The method returns the first line text regardless of content
        assert name == "123 Main St"


class TestMerchantResolverOCRCrossValidation:
    """Test OCR text cross-validation of ChromaDB matches.

    The resolver should reject matches whose merchant name has zero
    meaningful token overlap with the receipt's OCR text.  This catches
    metadata-poisoning and over-representation bugs (e.g. Sprouts
    dominating ChromaDB, wrong phone metadata).
    """

    @pytest.fixture
    def mock_dynamo_client(self):
        client = MagicMock()
        return client

    @pytest.fixture
    def mock_lines_client(self):
        return MagicMock()

    @pytest.fixture
    def resolver(self, mock_dynamo_client):
        return MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

    def _make_line(self, line_id: int, text: str, y: float = 0.1):
        line = MagicMock(spec=ReceiptLine, line_id=line_id, text=text)
        line.calculate_centroid.return_value = (0.5, y)
        return line

    # ------------------------------------------------------------------
    # Unit tests for _merchant_name_matches_receipt
    # ------------------------------------------------------------------

    def test_matching_merchant_passes(self, resolver):
        """Merchant name shares tokens with receipt text → True."""
        lines = [self._make_line(1, "AIM MAIL CENTER #18")]
        assert resolver._merchant_name_matches_receipt(
            "AIM Mail Center", lines
        )

    def test_non_matching_merchant_fails(self, resolver):
        """No token overlap → False."""
        lines = [self._make_line(1, "AIM MAIL CENTER #18")]
        assert not resolver._merchant_name_matches_receipt(
            "Sprouts Farmers Market", lines
        )

    def test_whole_foods_vs_mouthful_eatery(self, resolver):
        """Whole Foods receipt should reject Mouthful Eatery."""
        lines = [
            self._make_line(1, "WHOLE FOODS MARKET", y=0.1),
            self._make_line(2, "740 N MOORPARK RD", y=0.2),
        ]
        assert not resolver._merchant_name_matches_receipt(
            "Mouthful Eatery", lines
        )

    def test_empty_merchant_name_passes(self, resolver):
        """None/empty merchant name should pass (nothing to validate)."""
        lines = [self._make_line(1, "Some Store")]
        assert resolver._merchant_name_matches_receipt(None, lines)
        assert resolver._merchant_name_matches_receipt("", lines)

    def test_short_merchant_name_passes(self, resolver):
        """Single-token merchant names pass (too short to validate)."""
        lines = [self._make_line(1, "Something Else Entirely")]
        # "JOi" → only token is "joi" (3 chars) → 1 token < 2 → pass
        assert resolver._merchant_name_matches_receipt("JOi", lines)

    def test_tokenizer_ignores_short_words(self, resolver):
        """Tokens under 3 chars are ignored."""
        tokens = resolver._tokenize("A & B Grocery")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "grocery" in tokens

    # ------------------------------------------------------------------
    # Integration tests: full resolve() with cross-validation
    # ------------------------------------------------------------------

    def test_mismatched_merchant_rejected_falls_to_tier2(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """ChromaDB returns wrong merchant → rejected → falls to Tier 2."""
        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=2,
                extracted_data={"type": "phone", "value": "8054956229"},
            )
        ]
        lines = [
            self._make_line(1, "AIM MAIL CENTER #18", y=0.1),
            self._make_line(2, "(805) 495-6229", y=0.9),
        ]

        # ChromaDB returns "Sprouts Farmers Market" (wrong)
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-image",
                        "receipt_id": 5,
                        "merchant_name": "Sprouts Farmers Market",
                        "normalized_phone_10": "8054956229",
                    }
                ]
            ],
            "distances": [[0.1]],
        }
        mock_place = MagicMock()
        mock_place.place_id = "ChIJZxmMXO4k6IARlo_-qBA0nBQ"
        mock_place.merchant_name = "Sprouts Farmers Market"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        line_embeddings = {1: [0.1] * 1536, 2: [0.2] * 1536}

        tier2_result = MerchantResult(
            place_id="ChIJ_correct_aim",
            merchant_name="AIM Mail Center",
            confidence=0.8,
            resolution_tier="place_id_finder_agentic",
        )

        with patch.object(
            resolver, "_run_place_id_finder", return_value=tier2_result
        ):
            result = resolver.resolve(
                lines_client=mock_lines_client,
                lines=lines,
                words=words,
                image_id="test-image",
                receipt_id=1,
                line_embeddings=line_embeddings,
            )

        # Should get Tier 2 result, not the wrong Sprouts match
        assert result.merchant_name == "AIM Mail Center"
        assert result.resolution_tier == "place_id_finder_agentic"

    def test_correct_merchant_passes_cross_validation(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """ChromaDB returns correct merchant → passes → returned."""
        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=2,
                extracted_data={"type": "phone", "value": "8054956229"},
            )
        ]
        lines = [
            self._make_line(1, "Sprouts Farmers Market", y=0.1),
            self._make_line(2, "(805) 495-6229", y=0.9),
        ]

        # ChromaDB correctly returns "Sprouts Farmers Market"
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-image",
                        "receipt_id": 5,
                        "merchant_name": "Sprouts Farmers Market",
                        "normalized_phone_10": "8054956229",
                    }
                ]
            ],
            "distances": [[0.1]],
        }
        mock_place = MagicMock()
        mock_place.place_id = "ChIJZxmMXO4k6IARlo_-qBA0nBQ"
        mock_place.merchant_name = "Sprouts Farmers Market"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        line_embeddings = {1: [0.1] * 1536, 2: [0.2] * 1536}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="test-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should accept the correct match
        assert result.merchant_name == "Sprouts Farmers Market"
        assert result.resolution_tier == "chroma_phone"
        assert result.place_id == "ChIJZxmMXO4k6IARlo_-qBA0nBQ"

    def test_second_match_used_when_first_rejected(
        self, resolver, mock_lines_client, mock_dynamo_client
    ):
        """First match rejected, second match passes cross-validation."""
        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=2,
                extracted_data={"type": "phone", "value": "8054956229"},
            )
        ]
        lines = [
            self._make_line(1, "AIM MAIL CENTER #18", y=0.1),
            self._make_line(2, "(805) 495-6229", y=0.9),
        ]

        # ChromaDB returns 2 matches: wrong (Sprouts) then right (AIM)
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "sprouts-img",
                        "receipt_id": 5,
                        "merchant_name": "Sprouts Farmers Market",
                        "normalized_phone_10": "8054956229",
                    },
                    {
                        "image_id": "aim-img",
                        "receipt_id": 1,
                        "merchant_name": "AIM Mail Center",
                        "normalized_phone_10": "8054956229",
                    },
                ]
            ],
            "distances": [[0.1, 0.3]],
        }

        # DynamoDB returns different places depending on the receipt
        def _get_place(image_id, receipt_id):
            place = MagicMock()
            if image_id == "sprouts-img":
                place.place_id = "ChIJ_sprouts"
                place.merchant_name = "Sprouts Farmers Market"
            else:
                place.place_id = "ChIJ_aim_correct"
                place.merchant_name = "AIM Mail Center"
            return place

        mock_dynamo_client.get_receipt_place.side_effect = _get_place

        line_embeddings = {1: [0.1] * 1536, 2: [0.2] * 1536}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="test-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should pick AIM Mail Center (2nd match) not Sprouts (1st)
        assert result.merchant_name == "AIM Mail Center"
        assert result.resolution_tier == "chroma_phone"


class TestMerchantResolverErrorHandling:
    """Test error handling in MerchantResolver."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        return MagicMock()

    @pytest.fixture
    def mock_lines_client(self):
        """Create mock ChromaClient."""
        return MagicMock()

    def test_chroma_query_error_is_handled(
        self, mock_dynamo_client, mock_lines_client
    ):
        """Test that ChromaDB query errors are handled gracefully."""
        resolver = MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=1,
                extracted_data={"type": "phone", "value": "5551234567"},
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock ChromaDB to raise exception
        mock_lines_client.query.side_effect = Exception("ChromaDB error")

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="test-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should return empty result, not raise
        assert result.place_id is None

    def test_dynamo_lookup_error_is_handled(
        self, mock_dynamo_client, mock_lines_client
    ):
        """Test that DynamoDB lookup errors are handled gracefully."""
        resolver = MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=1,
                extracted_data={"type": "phone", "value": "5551234567"},
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        # Mock ChromaDB to return a match with distance
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-image",
                        "receipt_id": 99,
                        "normalized_phone_10": "5551234567",
                    }
                ]
            ],
            "distances": [[0.1]],
        }

        # Mock DynamoDB to raise exception
        mock_dynamo_client.get_receipt_place.side_effect = Exception(
            "DynamoDB error"
        )

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        result = resolver.resolve(
            lines_client=mock_lines_client,
            lines=lines,
            words=words,
            image_id="test-image",
            receipt_id=1,
            line_embeddings=line_embeddings,
        )

        # Should return empty result
        assert result.place_id is None

    def test_invalid_place_ids_are_filtered(
        self, mock_dynamo_client, mock_lines_client
    ):
        """Test that invalid place_ids are filtered out."""
        resolver = MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

        words = [
            MagicMock(
                spec=ReceiptWord,
                line_id=1,
                extracted_data={"type": "phone", "value": "5551234567"},
            )
        ]
        mock_line = MagicMock(spec=ReceiptLine, line_id=1, text="Store")
        mock_line.calculate_centroid.return_value = (0.5, 0.1)
        lines = [mock_line]

        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other",
                        "receipt_id": 99,
                        "normalized_phone_10": "5551234567",
                    }
                ]
            ],
            "distances": [[0.1]],
        }

        # Provide pre-cached line embeddings
        fake_embedding = [0.1] * 1536
        line_embeddings = {1: fake_embedding}

        # Test invalid place_id values
        for invalid_id in ["", "null", "NO_RESULTS", "INVALID"]:
            mock_place = MagicMock()
            mock_place.place_id = invalid_id
            mock_dynamo_client.get_receipt_place.return_value = mock_place

            result = resolver.resolve(
                lines_client=mock_lines_client,
                lines=lines,
                words=words,
                image_id="test-image",
                receipt_id=1,
                line_embeddings=line_embeddings,
            )

            assert result.place_id is None


# ======================================================================
# DynamoDB authoritative merchant_name
# ======================================================================


class TestGetPlaceFromDynamo:
    """Test _get_place_from_dynamo returning (place_id, merchant_name)."""

    @pytest.fixture
    def mock_dynamo_client(self):
        return MagicMock()

    @pytest.fixture
    def resolver(self, mock_dynamo_client):
        return MerchantResolver(
            dynamo_client=mock_dynamo_client,
            places_client=None,
        )

    def test_get_place_from_dynamo_returns_merchant_name(
        self, resolver, mock_dynamo_client
    ):
        """DynamoDB has both place_id and merchant_name."""
        mock_place = MagicMock()
        mock_place.place_id = "ChIJ_test"
        mock_place.merchant_name = "Corrected Name"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        place_id, merchant_name = resolver._get_place_from_dynamo(
            "img-1", 1
        )

        assert place_id == "ChIJ_test"
        assert merchant_name == "Corrected Name"

    def test_get_place_from_dynamo_no_merchant_name(
        self, resolver, mock_dynamo_client
    ):
        """DynamoDB has place_id but no merchant_name → falls back to None."""
        mock_place = MagicMock()
        mock_place.place_id = "ChIJ_test"
        mock_place.merchant_name = None
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        place_id, merchant_name = resolver._get_place_from_dynamo(
            "img-1", 1
        )

        assert place_id == "ChIJ_test"
        assert merchant_name is None

    def test_get_place_from_dynamo_invalid_place_id(
        self, resolver, mock_dynamo_client
    ):
        """Invalid place_id → (None, None)."""
        mock_place = MagicMock()
        mock_place.place_id = "NO_RESULTS"
        mock_place.merchant_name = "Some Name"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        place_id, merchant_name = resolver._get_place_from_dynamo(
            "img-1", 1
        )

        assert place_id is None
        assert merchant_name is None

    def test_similarity_search_prefers_dynamo_merchant_name(
        self, resolver, mock_dynamo_client
    ):
        """ChromaDB match has stale name, DynamoDB has corrected name → uses DynamoDB."""

        def _make_line(line_id, text, y=0.1):
            line = MagicMock(spec=ReceiptLine, line_id=line_id, text=text)
            line.calculate_centroid.return_value = (0.5, y)
            return line

        # Set up resolver state
        resolver._line_embeddings = {1: [0.1] * 1536}
        resolver._receipt_lines = [
            _make_line(1, "Coffee House Corrected", y=0.1),
        ]

        mock_lines_client = MagicMock()
        mock_lines_client.query.return_value = {
            "metadatas": [
                [
                    {
                        "image_id": "other-img",
                        "receipt_id": 2,
                        "merchant_name": "Stale Coffee Name",  # ChromaDB stale
                        "normalized_phone_10": None,
                    }
                ]
            ],
            "distances": [[0.1]],
        }

        # DynamoDB returns corrected merchant_name
        mock_place = MagicMock()
        mock_place.place_id = "ChIJ_coffee"
        mock_place.merchant_name = "Coffee House Corrected"
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        query_line = _make_line(1, "Coffee House Corrected")

        result = resolver._similarity_search_impl(
            lines_client=mock_lines_client,
            query_line=query_line,
            current_image_id="current-img",
            current_receipt_id=1,
            expected_phone=None,
            expected_address=None,
            resolution_tier="chroma_text",
        )

        assert result.place_id == "ChIJ_coffee"
        # Should use DynamoDB name, not the stale ChromaDB one
        assert result.merchant_name == "Coffee House Corrected"


# ======================================================================
# Write-time validation & module-level functions
# ======================================================================


class TestModuleLevelFunctions:
    """Test extracted module-level tokenize_text and merchant_name_matches_receipt."""

    def _make_line(self, line_id: int, text: str, y: float = 0.1):
        line = MagicMock(spec=ReceiptLine, line_id=line_id, text=text)
        line.calculate_centroid.return_value = (0.5, y)
        return line

    def test_tokenize_text_basic(self):
        """Module-level tokenize_text works correctly."""
        tokens = tokenize_text("Sprouts Farmers Market")
        assert "sprouts" in tokens
        assert "farmers" in tokens
        assert "market" in tokens

    def test_tokenize_text_ignores_short(self):
        """Tokens under 3 chars are ignored."""
        tokens = tokenize_text("A & B Grocery")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "grocery" in tokens

    def test_module_level_merchant_name_matches_receipt_pass(self):
        """Module-level function detects token overlap."""
        lines = [self._make_line(1, "AIM MAIL CENTER #18")]
        assert merchant_name_matches_receipt("AIM Mail Center", lines)

    def test_module_level_merchant_name_matches_receipt_fail(self):
        """Module-level function detects no token overlap."""
        lines = [self._make_line(1, "AIM MAIL CENTER #18")]
        assert not merchant_name_matches_receipt(
            "Sprouts Farmers Market", lines
        )

    def test_module_level_merchant_name_matches_receipt_none(self):
        """None merchant name passes."""
        lines = [self._make_line(1, "Store")]
        assert merchant_name_matches_receipt(None, lines)

    def test_module_level_merchant_name_matches_receipt_empty_lines(self):
        """Empty lines list passes (no receipt text to validate against)."""
        assert merchant_name_matches_receipt("Sprouts Farmers Market", [])


class TestWriteTimeValidationLogic:
    """Test the merchant_name_matches_receipt helper used by write-time validation.

    These tests verify the validation *logic* in isolation.  The actual
    write-time guard lives in ``_run_lines_pipeline_worker`` inside
    ``embedding_processor.py`` (lines 226-242) and calls the same function.
    """

    def _make_line(self, line_id: int, text: str, y: float = 0.1):
        line = MagicMock(spec=ReceiptLine, line_id=line_id, text=text)
        line.calculate_centroid.return_value = (0.5, y)
        return line

    def test_worker_validates_merchant_name(self):
        """Poisoned merchant_name gets replaced with None."""
        lines = [
            self._make_line(1, "AIM MAIL CENTER #18", y=0.1),
            self._make_line(2, "(805) 495-6229", y=0.9),
        ]
        # "Sprouts Farmers Market" has no overlap with "AIM MAIL CENTER"
        assert not merchant_name_matches_receipt(
            "Sprouts Farmers Market", lines
        )

    def test_worker_passes_valid_merchant_name(self):
        """Valid merchant_name passes write-time validation."""
        lines = [
            self._make_line(1, "Sprouts Farmers Market", y=0.1),
            self._make_line(2, "740 N MOORPARK RD", y=0.2),
        ]
        assert merchant_name_matches_receipt(
            "Sprouts Farmers Market", lines
        )

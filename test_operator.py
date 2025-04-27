import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import json
from agents import Agent, Runner, function_tool
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_label.data.places_api import PlacesAPI

GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]

print(
    PlacesAPI(GOOGLE_PLACES_API_KEY).search_by_text("Starbucks", 37.774929, -122.419418)
)


# Final metadata return tool
# @function_tool
# def tool_return_metadata(
#     place_id: str,
#     merchant_name: str,
#     address: str,
#     phone_number: str,
#     match_confidence: float,
#     matched_fields: List[str],
#     validated_by: str,
#     reasoning: str,
# ) -> Dict[str, Any]:
#     """
#     Return the final merchant metadata as a structured object.

#     Args:
#         place_id (str): Google Places place_id for the matched business.
#         merchant_name (str): The canonical merchant name.
#         address (str): The merchant's address.
#         phone_number (str): The merchant's phone number.
#         match_confidence (float): Confidence score for the match (0.0-1.0).
#         matched_fields (List[str]): List of receipt fields that were used to validate the match (e.g., ["name", "phone"]).
#         validated_by (str): The method or tool used for validation.
#         reasoning (str): Explanation of how the match was determined.
#     """
#     return {
#         "place_id": place_id,
#         "merchant_name": merchant_name,
#         "address": address,
#         "phone_number": phone_number,
#         "match_confidence": match_confidence,
#         "matched_fields": matched_fields,
#         "validated_by": validated_by,
#         "reasoning": reasoning,
#     }


# from receipt_label.merchant_validation.merchant_validation import (
#     extract_candidate_merchant_fields,
#     infer_merchant_with_gpt,
#     validate_match_with_gpt,
#     get_receipt_details,
# )

# google_places_api_key = os.environ["GOOGLE_PLACES_API_KEY"]


# # 2. Phone lookup
# @function_tool
# def tool_search_by_phone(phone: str) -> Dict[str, Any]:
#     """
#     Search Google Places by a phone number.
#     """
#     from receipt_label.merchant_validation.merchant_validation import PlacesAPI

#     return PlacesAPI(google_places_api_key).search_by_phone(phone)


# # 3. Address geocode & nearby
# @function_tool
# def tool_search_by_address(address: str) -> Dict[str, Any]:
#     """
#     Search Google Places by address and return the full place details payload.
#     """
#     from receipt_label.merchant_validation.merchant_validation import PlacesAPI

#     # Return the full Places API result for the address search
#     result = PlacesAPI(google_places_api_key).search_by_address(address)
#     return result


# @function_tool
# def tool_search_nearby(lat: float, lng: float, radius: float) -> List[Dict[str, Any]]:
#     """
#     Find nearby businesses given latitude, longitude, and radius.
#     """
#     from receipt_label.merchant_validation.merchant_validation import PlacesAPI

#     return PlacesAPI(google_places_api_key).search_nearby(
#         lat=lat, lng=lng, radius=radius
#     )


# @function_tool
# def tool_search_by_text(
#     query: str, lat: Optional[float] = None, lng: Optional[float] = None
# ) -> Dict[str, Any]:
#     """
#     Text‐search for a business name, with optional location bias.
#     """
#     from receipt_label.data.places_api import PlacesAPI

#     return PlacesAPI(google_places_api_key).search_by_text(query, lat, lng)


# TOOLS = [
#     tool_search_by_phone,
#     tool_search_by_address,
#     tool_search_nearby,
#     tool_search_by_text,
#     tool_return_metadata,
# ]

# agent = Agent(
#     name="ReceiptMerchantAgent",
#     instructions="""
# You are ReceiptMerchantAgent. Your goal is to assign the correct merchant to this receipt.
# You may call the following tools in any order:

# 1. **search_by_phone**: to look up a business by phone.
# 2. **search_by_address**: to geocode the receipt’s address.
# 3. **search_nearby**: to find businesses near a lat/lng.
# 4. **search_by_text**: to text‐search for a business name, biased by location.

# **Policy**:
# - First try phone lookup. If you get a business (non‐empty name & place_id), check if its name/address/phone closely matches the extracted fields.
# - If that fails, geocode → nearby search. Again validate on name/address/phone.
# - If still no good business, do a text search with the extracted or inferred business name.
# - Only if *all* Google‐search avenues fail, infer the merchant yourself *once* from the whole receipt (name, line‐items, totals, address block).
# - After deciding on the final metadata, call the function `tool_return_metadata`
#   with the exact values for each field (place_id, merchant_name, etc.) and then stop.
# """,
#     model="gpt-3.5-turbo",
#     tools=TOOLS,
# )

# image_id = "7ca97b51-7369-4321-bd01-0211f2bf2557"
# receipt_id = 2
# (
#     receipt,
#     receipt_lines,
#     receipt_words,
#     letters,
#     tags,
#     labels,
# ) = get_receipt_details(image_id, receipt_id)


# # Prepare the user input
# user_input = {
#     "image_id": image_id,
#     "receipt_id": receipt_id,
#     "raw_text": [line.text for line in receipt_lines],
#     "extracted_data": {
#         key: [w.text for w in words]
#         for key, words in extract_candidate_merchant_fields(receipt_words).items()
#     },
# }

# # Run the agent
# run_result = Runner.run_sync(
#     agent, [{"role": "user", "content": json.dumps(user_input)}]
# )


# # Extract structured metadata by parsing the function call arguments
# metadata = None
# for item in run_result.new_items:
#     raw = getattr(item, "raw_item", None)
#     # Check for a function call invocation for tool_return_metadata
#     if hasattr(raw, "name") and raw.name == "tool_return_metadata":
#         # raw.arguments contains the JSON string with the metadata
#         try:
#             metadata = json.loads(raw.arguments)
#         except Exception:
#             # Fallback to item.output if parsing fails
#             metadata = getattr(item, "output", None)
#         break

# if metadata is None:
#     raise RuntimeError("Agent did not call tool_return_metadata")

# meta = ReceiptMetadata(
#     image_id=image_id,
#     receipt_id=receipt_id,
#     place_id=metadata["place_id"],
#     merchant_name=metadata["merchant_name"],
#     address=metadata["address"],
#     phone_number=metadata["phone_number"],
#     match_confidence=metadata["match_confidence"],
#     matched_fields=metadata["matched_fields"],
#     timestamp=datetime.now(timezone.utc),
#     validated_by=metadata["validated_by"],
#     reasoning=metadata["reasoning"],
# )

# print(dict(meta))

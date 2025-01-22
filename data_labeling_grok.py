#!/usr/bin/env python3
"""
data_labeling_grok.py
---------------------
Example script that retrieves receipt OCR text from Dynamo, then uses
the Grok library (pygrok) to parse structured fields (store name, date, etc.).
After extracting fields, it tags the associated OCR words in Dynamo.
"""

import re
import math
import os
import json
from dotenv import load_dotenv
from pygrok import Grok
from dynamo import DynamoClient, ReceiptWord

RECEIPT_ID = 4
DISTANCE_THRESHOLD = 0.01

def distance(a, b):
    """Euclidean distance between two centroids a and b."""
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)

def find_nearest_ocr_word(centroid: dict, ocr_words: list[ReceiptWord], distance_threshold=0.01):
    """
    Returns the OCR Word object whose centroid is closest to `centroid`.
    If the closest distance is bigger than distance_threshold, returns None.
    """
    best_match = None
    best_dist = float("inf")

    cx, cy = centroid["x"], centroid["y"]
    for w in ocr_words:
        word_cx, word_cy = w.calculate_centroid()
        dist = math.sqrt((cx - word_cx) ** 2 + (cy - word_cy) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_match = w

    return best_match if best_dist <= distance_threshold else None

def tag_words_for_field(field_name: str, field_value: str, ocr_words: list[ReceiptWord]):
    """
    Given a field name (e.g. 'store_name') and the extracted field value,
    try to find matching OCR words by nearest-centroid logic or direct text match.
    Then update the tags for those words.
    
    If you want to do exact text matching or partial matches, you can modify this.
    Currently, we do a simplified approach: split by whitespace, then find the 
    nearest OCR word for each sub-token.
    """
    tokens = field_value.split()
    updated_words = []
    # For demonstration, assume approximate matching with nearest centroid if needed.
    # In reality, you might want more robust matching logic.
    for token in tokens:
        # We'll do a naive approach: find any word that matches this token ignoring punctuation
        # or fallback to a "no exact match found, skip" approach.
        # If your OCR text does not preserve spacing, consider your centroid-based approach.
        candidate = None
        for w in ocr_words:
            w_stripped = re.sub(r"[^A-Za-z0-9.\-_$]", "", w.text.lower())
            t_stripped = re.sub(r"[^A-Za-z0-9.\-_$]", "", token.lower())
            if w_stripped == t_stripped:
                candidate = w
                break

        if candidate:
            # Tag the word
            if field_name not in candidate.tags:
                candidate.tags.append(field_name)
            updated_words.append(candidate)
        else:
            # Fallback: skip or do nearest centroid, etc.
            # For brevity, we skip fallback here, but you can integrate your
            # centroid matching code if you prefer.
            pass

    return updated_words

def main():
    # Load environment and init DB
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    os.environ.pop("OPENAI_API_KEY", None)  # to ensure no conflict with old code
    load_dotenv(dotenv_path)

    dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))
    payload, lek = dynamo_client.listReceiptDetails()
    receipt_details = payload[RECEIPT_ID]
    words = receipt_details["words"]

    # Build a single text block from all OCR words
    # (This may or may not help with Grok, depending on your approach.)
    # You can also attempt line-by-line parsing if your receipts are line-oriented.
    combined_text = " ".join([w.text for w in words])

    # --------------------------------------------
    # 1) Define Grok patterns for your fields
    #    Adjust these to match your actual data
    # --------------------------------------------
    # Example patterns (very simplistic):
    #   Store name: a line that might say "Store: <something>"
    #   Date: something like "Date: 2023-01-15" or "2023/01/15"
    #   Phone: "Phone: (555) 555-1234" or "555-555-1234"
    #   Total: "Total: 24.99"
    # Adjust as needed or break it down line-by-line
    store_pattern = Grok(r"Store:\s*%{GREEDYDATA:store_name}")
    date_pattern = Grok(r"Date:\s*%{MONTHNUM:month}/%{MONTHDAY:day}/%{YEAR:year}")
    phone_pattern = Grok(r"(?:Phone|Ph|Tel):\s*%{PHONENUM:phone_number}")
    total_pattern = Grok(r"(?:Total|TOTAL):\s*\$?%{NUMBER:total_amount}")

    # --------------------------------------------
    # 2) Match patterns in combined text
    # --------------------------------------------
    store_match = store_pattern.match(combined_text)
    date_match = date_pattern.match(combined_text)
    phone_match = phone_pattern.match(combined_text)
    total_match = total_pattern.match(combined_text)

    # Pull out the fields
    store_name = store_match["store_name"] if store_match else ""
    if store_name:
        store_name = store_name.strip()

    # Build a synthetic "date" string if matched (some patterns break it down)
    extracted_date = ""
    if date_match:
        month = date_match.get("month", "")
        day = date_match.get("day", "")
        year = date_match.get("year", "")
        extracted_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    phone_number = phone_match["phone_number"] if phone_match else ""
    total_amount = total_match["total_amount"] if total_match else ""

    # --------------------------------------------
    # 3) Tag the words in Dynamo
    # --------------------------------------------
    words_to_update = []
    if store_name:
        words_to_update.extend(tag_words_for_field("store_name", store_name, words))
    if extracted_date:
        words_to_update.extend(tag_words_for_field("date", extracted_date, words))
    if phone_number:
        words_to_update.extend(tag_words_for_field("phone_number", phone_number, words))
    if total_amount:
        words_to_update.extend(tag_words_for_field("total_amount", total_amount, words))

    # Deduplicate any words we've tagged
    words_to_update = list(set(words_to_update))

    # Write the updated tags back to Dynamo
    for w in words_to_update:
        print(f"Updating word '{w.text}' with tags {w.tags}")
        dynamo_client.updateWord(w)

if __name__ == "__main__":
    main()
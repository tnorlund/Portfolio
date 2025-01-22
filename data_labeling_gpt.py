import re
import openai
import math
import os
import json
from dotenv import load_dotenv
from dynamo import DynamoClient, ReceiptWord

RECEIPT_ID = 4

def match_centroid_smart(centroid, ocr_words, gpt_value=None, distance_threshold=0.01):
    """
    Attempts to match a single GPT centroid to an OCR word by:
      1) If gpt_value is numeric, look for OCR words that parse to that same float value.
      2) If gpt_value is a string, you could do an exact or partial text match.
      3) If no match is found, fallback to the nearest centroid.

    Returns the matched Word object or None if nothing fits.
    """

    def distance(a, b):
        return math.sqrt((a["x"] - b["x"])**2 + (a["y"] - b["y"])**2)

    # First, if we didn’t receive a GPT value, just do nearest neighbor:
    if gpt_value is None:
        return find_nearest_ocr_word(centroid, ocr_words, distance_threshold)

    # Try parsing the GPT value as a float (e.g. for "24.98")
    numeric_val = None
    try:
        numeric_val = float(gpt_value)
    except ValueError:
        numeric_val = None  # Not numeric

    # ------------------------------------------------------------------------
    # 1) If GPT value is numeric, see if there's an OCR word that matches it.
    #    We parse the OCR word, remove non-digit/dot chars, and compare floats.
    # ------------------------------------------------------------------------
    if numeric_val is not None:
        candidate_words = []
        for w in ocr_words:
            stripped_text = re.sub(r"[^0-9.]", "", w.text)  # remove $, letters, etc.
            if not stripped_text:
                continue
            try:
                w_float = float(stripped_text)
                if abs(w_float - numeric_val) < 0.001:  # small tolerance
                    candidate_words.append(w)
            except ValueError:
                pass

        # If we found one or more numeric matches, pick whichever is
        # *closest by centroid*, provided it’s within the distance threshold.
        if candidate_words:
            cx, cy = centroid["x"], centroid["y"]
            best_match = None
            best_dist = float('inf')
            for w in candidate_words:
                w_cx, w_cy = w.calculate_centroid()
                d = math.sqrt((cx - w_cx)**2 + (cy - w_cy)**2)
                if d < best_dist:
                    best_dist = d
                    best_match = w

            # If the best match is within the threshold, return it:
            if best_dist <= distance_threshold:
                return best_match
            else:
                # If it’s out of threshold, you might do a fallback or just
                # return the best numeric match anyway. It's up to you:
                return best_match  # or return None if you want strictly within threshold

    # ------------------------------------------------------------------------
    # 2) For non-numeric GPT values (e.g., store_name), do an exact text match:
    # ------------------------------------------------------------------------
    gpt_str = str(gpt_value).strip()
    exact_matches = [w for w in ocr_words if w.text.strip() == gpt_str]
    if exact_matches:
        # Pick the one that’s closest by centroid
        cx, cy = centroid["x"], centroid["y"]
        best_match = None
        best_dist = float('inf')
        for w in exact_matches:
            w_cx, w_cy = w.calculate_centroid()
            d = math.sqrt((cx - w_cx)**2 + (cy - w_cy)**2)
            if d < best_dist:
                best_dist = d
                best_match = w
        # If it’s under threshold, return it
        if best_match and best_dist <= distance_threshold:
            return best_match

    # ------------------------------------------------------------------------
    # 3) If all else fails, fallback to nearest neighbor by coordinate:
    # ------------------------------------------------------------------------
    return find_nearest_ocr_word(centroid, ocr_words, distance_threshold)


def match_centroids_to_ocr_words(word_centroids: list[dict],
                                 ocr_words: list[ReceiptWord],
                                 gpt_value=None):
    """
    Return the list of actual Word objects matched to each centroid
    (rather than just text).
    """
    matched_words = []
    for centroid in word_centroids:
        # Use our "smart" approach, passing the GPT field’s value
        wobj = match_centroid_smart(centroid, ocr_words, gpt_value=gpt_value)
        matched_words.append(wobj)
    return matched_words


def find_nearest_ocr_word(centroid: dict, ocr_words: list[ReceiptWord], distance_threshold=0.01):
    """
    Returns the OCR Word object whose centroid is closest to `centroid`.
    If the closest distance is bigger than distance_threshold, returns None.
    """
    best_match = None
    best_dist = float('inf')

    # centroid is something like {"x": 0.54, "y": 0.62}
    cx, cy = centroid["x"], centroid["y"]

    for w in ocr_words:
        # Each `w` is presumably a Word object from your DB with .text and .calculate_centroid().
        # Or it might be a dict with {"text": ..., "centroid": {"x":..., "y":...}}
        word_cx, word_cy = w.calculate_centroid()

        dx = cx - word_cx
        dy = cy - word_cy
        dist = math.sqrt(dx * dx + dy * dy)

        if dist < best_dist:
            best_dist = dist
            best_match = w

    # If even the best match is too far, treat it as no match
    if best_dist > distance_threshold:
        return None

    return best_match

def match_centroids_to_ocr_texts(word_centroids: list[dict], ocr_words: list[ReceiptWord]) -> list[str]:
    """
    Given a list of centroids (from GPT JSON) and the full OCR word list (from DB),
    return a list of strings representing the matched OCR text for each centroid.
    """
    matched_texts = []
    for centroid in word_centroids:
        nearest = find_nearest_ocr_word(centroid, ocr_words)
        if nearest:
            matched_texts.append(nearest.text)
        else:
            matched_texts.append("[NO MATCH]")
    return matched_texts

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DYNAMO_DB_TABLE", None)
load_dotenv(dotenv_path)

openai.api_key = os.getenv("OPENAI_API_KEY")
dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))

payload, lek = dynamo_client.listReceiptDetails()
receipt_details = payload[RECEIPT_ID]
receipt = receipt_details["receipt"]
words = receipt_details["words"]

ocr_text = json.dumps(
    {
        "receipt": dict(receipt),
        "words": [
            {
                "text": word.text,
                "centroid": {
                    "x": word.calculate_centroid()[0],
                    "y": word.calculate_centroid()[1],
                },
            }
            for word in words
        ],
    }
)

prompt = f"""
You are a helpful assistant that extracts structured data from a receipt.
The receipt's OCR text is:

{ocr_text}

**Your task**: Identify the following fields and output them as valid JSON:
    - store_name (string)
    - date (string)
    - time (string)
    - phone_number (string)
    - total_amount (number)
    - items (array of objects with fields: "item_name" (string) and "price" (number))
    - taxes (number)
    - any other relevant details

Additionally, for **every field** you return, **please include**:
1) The field's **value** (e.g. "SPROUTS FARMERS MARKET").
2) An array of "word_centroids" that correspond to the OCR words. 
     - This array should list the (x, y) coordinates of each word that you used to form that field's value.
     - Use the same centroids from the "words" array above.

If a particular field is not found, return an empty string or null for that field.

**The JSON structure** should look like this (conceptually):
```json
{{
"store_name": {{
    "value": "...",
    "word_centroids": [
      {{"x": ..., "y": ...}},
      ...
    ]
  }},
...
"items": [
        {{
            "item_name": {{
                "value": "...",
                "word_centroids": [...]
            }},
            "price": {{
                "value": 0.0,
                "word_centroids": [...]
            }}
        }}
    ],
}}
```
IMPORTANT: Make sure your output is valid JSON, with double quotes around keys and strings.
"""


response = openai.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[
        {"role": "system", "content": "You extract structured data from text."},
        {"role": "user", "content": prompt},
    ],
)

# 1. Grab the raw text (the model's message content)
raw_message = response.choices[0].message.content

# 2. Remove the ```json ... ``` fences (if any)
json_text = re.sub(r"^```json|```$", "", raw_message, flags=re.MULTILINE).strip()

# 3. Parse the string as JSON
parsed = json.loads(json_text)

# 4. Write the parsed JSON to a file
with open(f"GPT_Output_Receipt_{RECEIPT_ID:05d}.json", "w") as f:
    json.dump(parsed, f, indent=4)

# Read the JSON file
with open(f"GPT_Output_Receipt_{RECEIPT_ID:05d}.json", "r") as f:
    parsed = json.load(f)
words_to_update = []
# iterate over the json key values
for key, value in parsed.items():
    # 1) If it's the "items" array, we must handle each item separately
    if key == "items" and isinstance(value, list):
        for i, item in enumerate(value):
            item_name = item.get("item_name", {})
            price = item.get("price", {})
            item_name_value = item_name.get("value", "")
            item_name_centroids = item_name.get("word_centroids", [])
            price_value = price.get("value", 0.0)
            price_centroids = price.get("word_centroids", [])
            # item_name_words = match_centroids_to_ocr_texts(item_name_centroids, words)
            # price_words = match_centroids_to_ocr_texts(price_centroids, words)
            item_name_objs = match_centroids_to_ocr_words(item_name_centroids, words, gpt_value=item_name_value)
            price_objs = match_centroids_to_ocr_words(price_centroids, words, gpt_value=price_value)

            for wobj in item_name_objs:
                if wobj:  # might be None if no match
                    wobj.tags.append(key)
                    wobj.tags.append("item_name")
                    words_to_update.append(wobj)
                else:
                    print("No match found for one of the item_name centroids")

            for wobj in price_objs:
                if wobj:
                    wobj.tags.append(key)
                    wobj.tags.append("item_price")
                    words_to_update.append(wobj)
                else:
                    print("No match found for one of the price centroids")
    
    # 2) Otherwise, if it's a dict with "word_centroids" (like store_name, date, etc.)
    elif isinstance(value, dict):
        field_value = value.get("value", "")
        word_centroids = value.get("word_centroids", [])
        words_in_response = str(field_value).split(" ")
        # The centroids describe the position of each word in the field value
        if len(words_in_response) == len(word_centroids):
            for word, centroid in zip(words_in_response, word_centroids):
                # Find the OCR word that matches the centroid
                nearest = find_nearest_ocr_word(centroid, words)
                if nearest:
                    if key in nearest.tags:
                        continue
                    nearest.tags.append(key)
                    words_to_update.append(nearest)
                else:
                    Warning(f"No OCR word found for centroid {centroid}")
        # The word is found multiple times in the field value
        else:
            for centroid in word_centroids:
                nearest = find_nearest_ocr_word(centroid, words)
                if nearest:
                    if key in nearest.tags:
                        continue
                    nearest.tags.append(key)
                    words_to_update.append(nearest)
                else:
                    print(f"No OCR word found for centroid {centroid}")

    else:
        # Potentially handle other structure, arrays, or unexpected data
        pass

# Update the words in the database
for word in words_to_update:
    print(f"Updating word {word.text} with tags {word.tags}")
    dynamo_client.updateWord(word)
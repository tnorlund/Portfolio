import re
import openai
import os
import json
from dotenv import load_dotenv
from dynamo import DynamoClient

RECEIPT_ID = 1

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

print(raw_message)

# 2. Remove the ```json ... ``` fences (if any)
json_text = re.sub(r"^```json|```$", "", raw_message, flags=re.MULTILINE).strip()

# 3. Parse the string as JSON
parsed = json.loads(json_text)

# 4. Write the parsed JSON to a file
with open(f"GPT_Output_Receipt_{receipt.id:05d}.json", "w") as f:
    json.dump(parsed, f, indent=4)

# Read the JSON file
with open(f"GPT_Output_Receipt_{RECEIPT_ID:05d}.json", "r") as f:
    parsed = json.load(f)

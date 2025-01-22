import re
import requests
import json
from dotenv import load_dotenv
import os
from dynamo import DynamoClient
from utils import load_env

# Load environment variables from .env file
load_dotenv()

RECEIPT_ID = 1

# Get the API token from environment variables
api_token = os.getenv("GROK_API_TOKEN")
if not api_token:
    raise ValueError("GROK_API_TOKEN not found in environment variables")
raw_bucket, lambda_function, dynamo_db_table = load_env()

# Use Dynamo to get
dynamo_client = DynamoClient(dynamo_db_table)
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

# Update the headers with the token from .env file
headers = {}
headers["Content-Type"] = "application/json"
headers["Authorization"] = f"Bearer {api_token}"

url = "https://api.x.ai/v1/chat/completions"

data = {
    "messages": [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": prompt},
    ],
    "model": "grok-beta",
    "stream": False,
    "temperature": 0,
}

response = requests.post(url, headers=headers, json=data)
status_code = response.status_code
if status_code != 200:
    raise ValueError(f"Request failed with status code {status_code}")

json_response = response.json()
if "error" in json_response:
    raise ValueError(f"Request failed with error: {response.json()['error']}")

if "choices" not in json_response:
    raise ValueError(f"Response does not contain 'choices' key {json_response.keys()}")

first_choice = json_response["choices"][0]

if "message" not in first_choice:
    raise ValueError(f"Response does not contain 'message' key {first_choice.keys()}")

print(first_choice["message"])

content = first_choice["message"]["content"]


# Save the response to a ".txt" file
with open(f"data_labeling_grok_response_{RECEIPT_ID:05d}.txt", "w") as file:
    file.write(content)

json_text = re.sub(r"^```json|```$", "", content, flags=re.MULTILINE).strip()

try:
    parsed = json.loads(json_text)
except json.JSONDecodeError as e:
    raise ValueError(f"Could not parse JSON: {e}")

# Save the parsed JSON to a ".json" file
with open(f"data_labeling_grok_response_{RECEIPT_ID:05d}.json", "w") as file:
    json.dump(parsed, file, indent=2)

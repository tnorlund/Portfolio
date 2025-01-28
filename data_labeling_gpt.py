import re
import math
import os
import requests
import json
from dotenv import load_dotenv
from dynamo import DynamoClient, ReceiptWord, ReceiptWordTag
from utils import llm_prompt, parse_llm_results
from datetime import datetime

RECEIPT_ID = 4
DEBUG = False
file_name = f"debug/receipt_result_{RECEIPT_ID:05d}.json"
prompt_file_name = f"debug/receipt_prompt_{RECEIPT_ID:05d}.txt"

def can_be_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DYNAMO_DB_TABLE", None)
load_dotenv(dotenv_path)

dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))

payload, lek = dynamo_client.listReceiptDetails()
receipt_details = payload[RECEIPT_ID]
receipt = receipt_details["receipt"]
words = receipt_details["words"]

print(f"https://dev.tylernorlund.com/{receipt.cdn_s3_key}")

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
}

url = "https://api.openai.com/v1/chat/completions"

payload = {
    "model": "gpt-3.5-turbo",
    "messages": [
        {"role": "system", "content": "You extract structured data from text."},
        {"role": "user", "content": llm_prompt(receipt, words)},
    ],
}

# write prompt to file
with open(prompt_file_name, "w") as f:
    json.dump(llm_prompt(receipt, words), f, indent=4)


if not DEBUG:
    response = requests.post(url, headers=headers, json=payload)

    raw_message = response.json()["choices"][0]["message"]["content"]

    json_parsable = False
    try:
        parsed = json.loads(raw_message)
        json_parsable = True
    except json.JSONDecodeError:
        pass

    #  Check to see if '```json' is in the string
    if not json_parsable and "```json" in raw_message:
        raw_message = raw_message.replace("```json", "")

    # Check if the '```' is in the string
    if not json_parsable and "```" in raw_message:
        raw_message = raw_message.replace("```", "")

    # Try to parse the JSON again
    try:
        parsed = json.loads(raw_message)
        json_parsable = True
    except json.JSONDecodeError:
        pass

    if not json_parsable:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"failure/{timestamp}_json_parse_fail.txt"
        with open(file_name, "w") as f:
            f.write(raw_message)
        raise ValueError(f"Failed to parse JSON response from GPT-3 chat completion. See {file_name}")

    with open(file_name, "w") as f:
        json.dump(parsed, f, indent=4)
else:
    with open(file_name, "r") as f:
        parsed = json.load(f)

results = parse_llm_results(parsed)

no_match = []
words_to_update = []
word_tags = []
for result in results:
    # Check to see if the number of word centroids matches the number of words in the result
    if not result.is_number and result.num_word_centroids == len(result.value.split(" ")):
        # match the word centroids to the words in the result
        word_split = result.value.split(" ")
        for word_centroid in result.word_centroids:
            word_centroid_tuple = word_centroid['x'], word_centroid['y']
            matched_words = [word for word in words if word.calculate_centroid() == word_centroid_tuple]
            if len(matched_words) == 0:
                print(f"Could not find word with centroid {word_centroid}")
                continue
            if len(matched_words) > 1:
                print(f"Found multiple words with centroid {word_centroid}")
                continue
            matched_word = matched_words[0]
            matched_word_text = word_split.pop(0)
            if matched_word.text != matched_word_text:
                print("not a number")
                print(f"Word text mismatch: {matched_word.text} != {matched_word_text}")
                continue

            matched_word.tags.extend(result.tag)
            words_to_update.append(matched_word)
            for tag in result.tag:
                word_tags.append(ReceiptWordTag(
                    receipt_id=receipt.id,
                    image_id=receipt.image_id,
                    line_id=matched_word.line_id,
                    word_id=matched_word.id,
                    tag=tag,
                    timestamp_added=datetime.now().isoformat()
                ))

    elif result.is_number and result.num_word_centroids == 1:
        word_centroid = result.word_centroids[0]
        word_centroid_tuple = word_centroid['x'], word_centroid['y']
        matched_words = [word for word in words if word.calculate_centroid() == word_centroid_tuple]
        if len(matched_words) == 0:
            print(f"Could not find word with centroid {word_centroid}")
            continue
        if len(matched_words) > 1:
            print(f"Found multiple words with centroid {word_centroid}")
            continue
        matched_word = matched_words[0]
        matched_word_text = result.value
        if not can_be_float(matched_word.text) or not can_be_float(matched_word_text):
            print(
                f"Word text: {matched_word.text}",
                f"Result value: {result.value}",
            )
            continue
        if float(matched_word.text) != float(matched_word_text):
            print(
                f"Word text mismatch: {matched_word.text} != {matched_word_text}"
            )
            continue
        matched_word.tags.append(result.value)
        words_to_update.append(matched_word)
        word_tags.append(ReceiptWordTag(
            receipt_id=receipt.id,
            image_id=receipt.image_id,
            line_id=matched_word.line_id,
            word_id=matched_word.id,
            tag=result.key,
            timestamp_added=datetime.now().isoformat()
        ))
    else:
        no_match.append(result)
    
print(len(words_to_update), len(word_tags))

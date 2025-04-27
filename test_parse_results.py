from pathlib import Path
import json
from dataclasses import dataclass, asdict
from receipt_label.utils import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()


@dataclass
class ParsedResult:
    custom_id: str
    is_valid: bool
    correct_label: str | None
    rationale: str | None
    image_id: str
    receipt_id: str
    line_id: str
    word_id: str
    label: str


file = Path(
    "/Users/tnorlund/Downloads/batch_680af9a5e8d88190b5bb65b48d05e0ad_output.jsonl"
)

# print(file)
parsed_results = []

with file.open() as f:
    for line in f:
        try:
            data = json.loads(line)
            custom_id = data["custom_id"]
            split_custom_id = custom_id.split("#")
            image_id = split_custom_id[1]
            receipt_id = int(split_custom_id[3])
            line_id = int(split_custom_id[5])
            word_id = int(split_custom_id[7])
            label = split_custom_id[9]

            response_text = data["response"]["body"]["choices"][0]["message"][
                "function_call"
            ]["arguments"]
            response_obj = json.loads(response_text)
            parsed_results.append(
                ParsedResult(
                    custom_id=custom_id,
                    is_valid=response_obj.get("is_valid"),
                    correct_label=response_obj.get("correct_label"),
                    rationale=(
                        response_obj.get("rationale").strip()
                        if response_obj.get("rationale")
                        else None
                    ),
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_id,
                    word_id=word_id,
                    label=label,
                )
            )
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Skipping line due to error: {e}")

# Display results
valid_results = [result for result in parsed_results if result.is_valid]
invalid_results = [result for result in parsed_results if not result.is_valid]

print(f"Valid results: {len(valid_results)}")
print(f"Invalid results: {len(invalid_results)}")

for result in valid_results:
    word = dynamo_client.getReceiptWord(
        image_id=result.image_id,
        receipt_id=result.receipt_id,
        line_id=result.line_id,
        word_id=result.word_id,
    )
    print(f"Word: {word.text}")
    print(f"Original: {result.label}")
    print(f"Suggestion: {result.correct_label}")
    print(f"Rationale: {result.rationale}")
    print()

# for result in invalid_results:
#     word = dynamo_client.getReceiptWord(
#         image_id=result.image_id,
#         receipt_id=result.receipt_id,
#         line_id=result.line_id,
#         word_id=result.word_id,
#     )
#     print(f"\n--- Invalid Label ---")
#     print(f"Word         : {word.text}")
#     print(f"Original     : {result.label}")
#     print(f"Suggestion   : {result.correct_label}")
#     print(f"Rationale    : {result.rationale}")

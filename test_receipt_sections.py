import json
from receipt_dynamo.entities import ReceiptWord
from receipt_label.utils import get_clients
from collections import Counter
import logging

image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1

dynamo_client, openai_client, pinecone_index = get_clients()

merchant_name = "VONS"
metadatas, last_evaluated_key = dynamo_client.getReceiptMetadatasByMerchant(
    merchant_name
)
print(f"Found {len(metadatas)} metadatas for {merchant_name}")
section_mapping = {}
for metadata in metadatas:
    receipt_id = metadata.receipt_id
    image_id = metadata.image_id
    receipt, lines, words, letters, tags, labels = dynamo_client.getReceiptDetails(
        image_id, receipt_id
    )
    print(f"Receipt {receipt_id} has {len(lines)} lines")

    # --- Begin OpenAI section categorization ---
    # Prepare line data for OpenAI, including spatial centroid
    lines_data = []
    for line in lines:
        # Calculate spatial centroid for the line
        x_center, y_center = line.calculate_centroid()
        lines_data.append(
            {
                "line_id": line.line_id,
                "text": line.text,
                "centroid": {"x": x_center, "y": y_center},
            }
        )

    system_prompt = (
        "You are a receipt parser. Given a JSON list of lines from a receipt "
        "with line_id and text, assign each line_id to a section."
        "Respond with a JSON object mapping line IDs to section names."
    )
    user_prompt = json.dumps(lines_data)

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        functions=[
            {
                "name": "label_sections",
                "description": "Map each receipt line ID to a section",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "line_id": {"type": "integer"},
                                    "section": {
                                        "type": "string",
                                    },
                                },
                                "required": ["line_id", "section"],
                            },
                        }
                    },
                    "required": ["sections"],
                },
            }
        ],
        function_call={"name": "label_sections"},
    )

    # Parse structured function call arguments
    func_call = response.choices[0].message.function_call
    args = json.loads(func_call.arguments)
    section_map = {item["line_id"]: item["section"] for item in args["sections"]}
    print("Line-to-Section Mapping:")
    print(json.dumps(section_map, indent=2))
    # Store mapping in the nested section_mapping dict
    if image_id not in section_mapping:
        section_mapping[image_id] = {}
    section_mapping[image_id][receipt_id] = section_map

    # --- End OpenAI section categorization ---

# Print all section mappings after the loop
print("All section mappings:")

# --- Begin comparing unique sections across receipts ---
# Flatten mappings to per-receipt unique section sets
sections_per_receipt = {}
for img_id, receipts in section_mapping.items():
    for rid, mapping in receipts.items():
        sections_per_receipt[rid] = set(mapping.values())

print("\nUnique sections per receipt:")
for rid, secs in sections_per_receipt.items():
    print(f"Receipt {rid}: {sorted(secs)}")

if sections_per_receipt:
    # Compute common and all sections
    common_sections = set.intersection(*sections_per_receipt.values())
    all_sections = set.union(*sections_per_receipt.values())
    print(f"\nCommon sections across receipts: {sorted(common_sections)}")
    print(f"All observed sections: {sorted(all_sections)}")
    # Show per-receipt differences
    print("\nSections unique to each receipt:")
    for rid, secs in sections_per_receipt.items():
        unique_to_rid = secs - common_sections
        print(f"Receipt {rid} unique: {sorted(unique_to_rid)}")

# --- End comparing unique sections ---

# --- Begin ChatGPT section merge suggestions ---
# Prepare list of observed section names
section_names = sorted(all_sections)

system_prompt = (
    "You are a helpful assistant that groups receipt section names into clusters of "
    "similar categories that should be merged. Given a list of section names, "
    "suggest clusters where each cluster is a list of names that can be unified."
)
user_prompt = json.dumps(section_names)

response = openai_client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    functions=[
        {
            "name": "suggest_section_merges",
            "description": "Clusters section names that should be merged",
            "parameters": {
                "type": "object",
                "properties": {
                    "clusters": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    }
                },
                "required": ["clusters"],
            },
        }
    ],
    function_call={"name": "suggest_section_merges"},
)

# Parse structured function call arguments
func_call = response.choices[0].message.function_call
args = json.loads(func_call.arguments)
clusters = args.get("clusters", [])

print("\nSuggested clusters of sections to merge:")
print(json.dumps(clusters, indent=2))
# --- End ChatGPT section merge suggestions ---

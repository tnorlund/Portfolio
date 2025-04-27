import json
from receipt_label.utils.clients import get_clients
from rapidfuzz import fuzz
from collections import defaultdict


def fuzzy_cluster(names: list[str], threshold: int = 85) -> list[list[str]]:
    clusters: list[list[str]] = []
    for name in names:
        placed = False
        for cluster in clusters:
            # check against the first member (single-link)
            if fuzz.ratio(name, cluster[0]) >= threshold:
                cluster.append(name)
                placed = True
                break
        if not placed:
            clusters.append([name])
    return clusters


dynamo_client, openai_client, pinecone_index = get_clients()

receipt_metadatas, last_evaluated_key = dynamo_client.listReceiptMetadatas()

# Separate those with Place ID from those without
receipt_metadatas_with_place_id = [
    receipt_metadata
    for receipt_metadata in receipt_metadatas
    if receipt_metadata.place_id != ""
]
receipt_metadatas_without_place_id = [
    receipt_metadata
    for receipt_metadata in receipt_metadatas
    if receipt_metadata.place_id == ""
]

print(f"Found {len(receipt_metadatas_with_place_id)} receipt metadatas with place ID")
print(
    f"Found {len(receipt_metadatas_without_place_id)} receipt metadatas without place ID"
)

unique_names_with_place_id = list(
    dict.fromkeys([rm.merchant_name for rm in receipt_metadatas_with_place_id])
)
unique_names_no_place_id = list(
    dict.fromkeys([rm.merchant_name for rm in receipt_metadatas_without_place_id])
)
fuzzy_clusters = fuzzy_cluster(unique_names_no_place_id)

print(f"Found {len(fuzzy_clusters)} fuzzy clusters")
# Build normalized → records map, here "normalized" = the raw name itself
name_to_records = defaultdict(list)
for rm in receipt_metadatas_without_place_id:
    name_to_records[rm.merchant_name].append(rm)

# Turn clusters of strings into clusters of ReceiptMetadata objects
record_clusters = []
for cluster in fuzzy_clusters:
    records = []
    for name in cluster:
        records.extend(name_to_records[name])
    record_clusters.append(records)


# 1) Define the function schema for canonical choice
functions = [
    {
        "name": "chooseCanonical",
        "description": "Given a set of known merchant names and new variant clusters, align or pick canonical names.",
        "parameters": {
            "type": "object",
            "properties": {
                "alignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "variant_cluster": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "One group of new merchant variants.",
                            },
                            "canonical": {
                                "type": ["string", "null"],
                                "enum": unique_names_with_place_id + [None],
                                "description": "Must be one of the known merchants or null.",
                            },
                        },
                        "required": ["variant_cluster", "canonical"],
                    },
                }
            },
            "required": ["alignments"],
        },
    }
]

# 2) Build your payload
payload = {
    "known_merchants": unique_names_with_place_id,  # e.g. ["Costco Wholesale", "Sprouts Farmers Market", …]
    "variant_clusters": fuzzy_clusters,  # e.g. [["COSTCO-WHOLESALE","COSTCO WHOLESALE"], …]
}

# 3) Construct the messages
messages = [
    {
        "role": "system",
        "content": (
            "You are a data-cleansing assistant. "
            "I have a list of already-canonicalized merchants from Google Places, "
            "and I have clusters of new name variants that need to be aligned. "
            "For each variant cluster, you should:\n"
            "  1. If it matches one of the known merchants (case- and punctuation-insensitive), "
            "return that exact known name.\n"
            "  2. Else, pick a human-friendly canonical name for the cluster.\n"
            "  3. If you are not confident, return null."
        ),
    },
    {"role": "user", "content": json.dumps(payload, indent=2)},
]

# 4) Call GPT with function calling
response = openai_client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=messages,
    functions=functions,
    function_call={"name": "chooseCanonical"},
)

# 5) Parse out the function call arguments
args = json.loads(response.choices[0].message.function_call.arguments)
# args["alignments"] will be a list of { "variant_cluster": [...], "canonical": "..." or null }

# Example of how you might iterate the results:
for entry in args["alignments"]:
    cluster = entry["variant_cluster"]
    canon = entry["canonical"]  # may be None
    print(f"Cluster {cluster!r} → canonical: {canon!r}")

from fuzzywuzzy import fuzz
from collections import defaultdict
import json
from datetime import datetime
from receipt_dynamo.entities import ReceiptMetadata
import re
from itertools import combinations
import copy


# --- Preprocessing & Normalization ---


def normalize_text(text):
    """Applies standard normalization for canonical display."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    # Remove redundant whitespace
    text = " ".join(text.split())
    # Optional: Title case for display?
    # text = text.title()
    return text


def normalize_address(address):
    """Applies more specific normalization for addresses."""
    if not isinstance(address, str):
        return ""
    address = address.lower()
    # Standardize street types
    address = re.sub(r"\bst\.?\b", "street", address)
    address = re.sub(r"\bave\.?\b", "avenue", address)
    address = re.sub(r"\bblvd\.?\b", "boulevard", address)
    address = re.sub(r"\brd\.?\b", "road", address)
    address = re.sub(r"\bdr\.?\b", "drive", address)
    address = re.sub(r"\bln\.?\b", "lane", address)
    address = re.sub(r"\bct\.?\b", "court", address)
    address = re.sub(r"\bsq\.?\b", "square", address)
    # Standardize state abbreviations (add more as needed)
    address = re.sub(r"\bca\b", "ca", address)  # Keep lowercase for consistency
    address = re.sub(r"\bmd\b", "md", address)
    address = re.sub(r"\bmn\b", "mn", address)
    # Remove punctuation
    address = re.sub(r"[.,!?;:\'\"()]", "", address)
    # Standardize country
    address = re.sub(r"\busa\b", "usa", address)
    address = re.sub(r"\bunited states\b", "usa", address)
    # Remove extra whitespace
    address = " ".join(address.split())
    return address


def preprocess_for_comparison(text):
    """Minimal preprocessing used only for similarity checks."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = re.sub(r"[.,!?;:\'\"()]", "", text)
    text = " ".join(text.split())
    return text


# --- Similarity Calculation ---


def get_name_similarity(name1, name2):
    p_name1 = preprocess_for_comparison(name1)
    p_name2 = preprocess_for_comparison(name2)
    if not p_name1 or not p_name2:
        return 0
    return fuzz.token_set_ratio(p_name1, p_name2)


def get_address_similarity(addr1, addr2):
    # Use normalized address for similarity to catch more variations
    p_addr1 = normalize_address(addr1)
    p_addr2 = normalize_address(addr2)
    if not p_addr1 or not p_addr2:
        return 0
    return fuzz.token_set_ratio(p_addr1, p_addr2)


def get_phone_similarity(ph1, ph2):
    p_ph1 = re.sub(r"\D", "", ph1) if ph1 else ""
    p_ph2 = re.sub(r"\D", "", ph2) if ph2 else ""
    if not p_ph1 or not p_ph2 or len(p_ph1) < 7 or len(p_ph2) < 7:  # Basic sanity check
        return 0
    # Compare last 7 or 10 digits? For now, exact match
    return 100 if p_ph1 == p_ph2 else 0


# --- Data Loading ---


def load_metadata():
    metadata_list = []
    with open("receipt_metadatas.ndjson", "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                # Ensure timestamp is converted correctly if needed by ReceiptMetadata
                if "timestamp" in data and isinstance(data["timestamp"], str):
                    # Handle potential timezone info if present
                    if data["timestamp"].endswith("Z"):
                        data["timestamp"] = data["timestamp"].replace("Z", "+00:00")
                    elif (
                        "+" not in data["timestamp"]
                        and "-" not in data["timestamp"][10:]
                    ):  # Quick check if timezone offset seems missing
                        # Attempt parsing without timezone, then assume UTC or local?
                        # Or require ISO format with offset. For simplicity, assume offset exists or fromisoformat handles it.
                        pass
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])

                # Handle potential missing optional fields for the constructor
                metadata = ReceiptMetadata(
                    image_id=data.get(
                        "image_id", "missing_image_id"
                    ),  # Provide defaults or handle missing data
                    receipt_id=data.get("receipt_id", 0),  # Provide defaults
                    place_id=data.get("place_id", ""),  # Use empty string if missing
                    merchant_name=data.get("merchant_name", ""),
                    match_confidence=data.get("match_confidence", 0.0),
                    matched_fields=data.get("matched_fields", []),
                    timestamp=data.get(
                        "timestamp", datetime.now()
                    ),  # Provide default timestamp
                    merchant_category=data.get("merchant_category", ""),
                    address=data.get("address", ""),
                    phone_number=data.get("phone_number", ""),
                    validated_by=data.get("validated_by", ""),
                    reasoning=data.get("reasoning", ""),
                )
                metadata_list.append(metadata)
            except Exception as e:
                print(
                    f"Warning: Skipping line due to error: {e} - Line: {line.strip()}"
                )
    return metadata_list


# --- Clustering Logic ---


def cluster_by_metadata(metadata_list: List[ReceiptMetadata]):
    clusters_by_place_id = defaultdict(list)
    records_without_place_id = []

    # --- Pass 1: Group by place_id ---
    for record in metadata_list:
        # Ensure place_id is a string and not empty before using it as a key
        place_id = getattr(record, "place_id", None)
        if place_id and isinstance(place_id, str) and place_id.strip():
            clusters_by_place_id[place_id.strip()].append(record)
        else:
            records_without_place_id.append(record)

    final_clusters = list(clusters_by_place_id.values())

    # --- Pass 2: Cluster remaining records by name + address/phone ---
    processed_indices = set()
    remaining_clusters = []

    for i in range(len(records_without_place_id)):
        if i in processed_indices:
            continue

        current_cluster = [records_without_place_id[i]]
        processed_indices.add(i)

        for j in range(i + 1, len(records_without_place_id)):
            if j in processed_indices:
                continue

            record1 = records_without_place_id[i]
            record2 = records_without_place_id[j]

            # High confidence name match required
            name_sim = get_name_similarity(record1.merchant_name, record2.merchant_name)
            if name_sim < 90:  # Stricter threshold for name when place_id is missing
                continue

            # Require strong address OR phone match
            addr_sim = get_address_similarity(record1.address, record2.address)
            phone_sim = get_phone_similarity(record1.phone_number, record2.phone_number)

            # Allow match if name is very similar AND (address is similar OR phone matches)
            if addr_sim >= 85 or phone_sim == 100:
                current_cluster.append(record2)
                processed_indices.add(j)

        remaining_clusters.append(current_cluster)

    final_clusters.extend(remaining_clusters)
    return final_clusters


def choose_canonical_metadata(cluster_members: List[ReceiptMetadata]):
    """Chooses the best representative record based on source priority and completeness."""

    SOURCE_PRIORITY = {
        "ADDRESS_LOOKUP": 5,
        "NEARBY_LOOKUP": 4,
        "TEXT_SEARCH": 3,
        "PHONE_LOOKUP": 2,
        "": 0,  # Handle empty string
        None: 0,  # Handle None
    }
    DEFAULT_PRIORITY = 1  # For any other source

    def get_score(record):
        validated_by = getattr(record, "validated_by", None)
        source_score = SOURCE_PRIORITY.get(validated_by, DEFAULT_PRIORITY)
        confidence_score = getattr(record, "match_confidence", 0.0)
        has_address = bool(getattr(record, "address", ""))
        has_phone = bool(getattr(record, "phone_number", ""))
        # Use negative name length to prefer shorter names among ties
        name_len_score = -len(normalize_text(getattr(record, "merchant_name", "")))

        # Prioritize source, then confidence, then completeness, then name length
        return (source_score, confidence_score, has_address, has_phone, name_len_score)

    if not cluster_members:
        return None

    # Separate records with and without place_id
    with_place_id = [m for m in cluster_members if getattr(m, "place_id", None)]
    without_place_id = [m for m in cluster_members if not getattr(m, "place_id", None)]

    # Prefer records with place_id if any exist
    candidates = with_place_id if with_place_id else without_place_id

    if not candidates:
        return None  # Should not happen if cluster_members is not empty

    # Find the best candidate based on the scoring function
    best_record = max(candidates, key=get_score)

    return best_record


# --- Main Execution & Output ---


def main():
    all_metadata = load_metadata()
    print(f"Loaded {len(all_metadata)} metadata records")

    if not all_metadata:
        print("No metadata records loaded. Exiting.")
        return

    clusters = cluster_by_metadata(all_metadata)

    print(f"\nFound {len(clusters)} clusters (including singletons)")
    print("\nCleaned Merchant Clusters (Consolidated):")
    print("=========================================")

    consolidated_count = 0
    final_unique_entities = 0

    # Process clusters to get canonical representations
    canonical_representations = []
    for members in clusters:
        if members:
            canonical_record = choose_canonical_metadata(members)
            if canonical_record:
                canonical_name = getattr(canonical_record, "merchant_name", "N/A")
                # Get the original address from the canonical record
                canonical_orig_address = getattr(canonical_record, "address", "N/A")
                # Also get the normalized version for potential use/comparison
                canonical_norm_address = normalize_address(canonical_orig_address)
                canonical_phone = getattr(canonical_record, "phone_number", "N/A")
                canonical_place_id = getattr(canonical_record, "place_id", "None")
                canonical_representations.append(
                    {
                        "name": canonical_name,
                        "place_id": canonical_place_id,
                        "orig_address": canonical_orig_address,  # Store original address
                        "norm_address": canonical_norm_address,  # Store normalized address
                        "phone": canonical_phone,
                        "members": members,
                    }
                )

    # Sort final representations by cluster size, then name
    canonical_representations.sort(
        key=lambda item: (-len(item["members"]), item["name"].lower())
    )

    for canonical in canonical_representations:
        members = canonical["members"]
        if len(members) > 1:
            final_unique_entities += 1
            consolidated_count += len(members) - 1
            print(f"\nCanonical Name: {canonical['name']}")
            print(f"  Place ID: {canonical['place_id']}")
            # Print the ORIGINAL address from the best source
            print(f"  Canonical Addr: {canonical['orig_address']}")
            # Optionally print the normalized one too
            # print(f"  Norm Address: {canonical['norm_address']}")
            print(f"  Phone: {canonical['phone']}")
            print(f"  Includes ({len(members)} total):")

            sorted_members = sorted(
                members, key=lambda m: getattr(m, "merchant_name", "").lower()
            )
            for record in sorted_members:
                print(
                    f"    - Orig Name: {getattr(record, 'merchant_name', 'N/A')} "
                    f"(Place ID: {getattr(record, 'place_id', 'None')}, "
                    f"Addr: {getattr(record, 'address', 'N/A')}, "
                    f"ValidBy: {getattr(record, 'validated_by', 'N/A')})"
                )
        else:
            final_unique_entities += 1
            # Optionally print singletons
            # print(f"\nSingle Name: {canonical['name']}")
            # print(f"  Place ID: {canonical['place_id']}")
            # print(f"  Canonical Addr: {canonical['orig_address']}")
            pass

    print("\n--- Summary ---")
    print(f"Original unique-ish records loaded: {len(all_metadata)}")
    print(f"Final unique entities after clustering: {final_unique_entities}")
    print(f"Records consolidated: {consolidated_count}")


if __name__ == "__main__":
    main()

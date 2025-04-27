import json
from receipt_dynamo.entities import ReceiptWord

# from receipt_label.submit_embedding_batch.submit_batch import get_hybrid_context
from receipt_label.utils import get_clients
from collections import Counter
import logging

# Core labels for validation prompts
CORE_LABELS = [
    # Merchant & store info
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    # Location/address (either as one line or broken out)
    "ADDRESS_LINE",  # or, for finer breakdown:
    # "ADDRESS_NUMBER",
    # "STREET_NAME",
    # "CITY",
    # "STATE",
    # "POSTAL_CODE",
    # Transaction info
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",  # if you want to distinguish coupons vs. generic discounts
    # Line‑item fields
    "PRODUCT_NAME",  # or ITEM_NAME
    "QUANTITY",  # or ITEM_QUANTITY
    "UNIT_PRICE",  # or ITEM_PRICE
    "LINE_TOTAL",  # or ITEM_TOTAL
    # Totals & taxes
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",  # or TOTAL
]

# Suppress HTTPX and OpenAI informational logging to keep stdout clean
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


dynamo_client, openai_client, pinecone_index = get_clients()


def get_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """
    Call OpenAI's embeddings endpoint and return the embedding vector for `text`.
    """
    # Assuming get_clients() returns (dynamo, openai_client, pinecone_index)
    _, openai_client, _ = get_clients()
    response = openai_client.embeddings.create(input=text, model=model)
    # The response.data is a list; take the first item’s embedding
    return response.data[0].embedding


def fetch_neighbors(embedding, merchant_name, view, top_k):
    return pinecone_index.query(
        vector=embedding,
        top_k=top_k,
        include_metadata=True,
        filter={"merchant_name": {"$eq": merchant_name}, "view": {"$eq": view}},
    ).matches


def format_prompt(
    word,
    # context_str,
    # ctx_neighbors,
    # word_neighbors,
    label_text,
    merchant_name,
    # receipt_id,
):
    prompt = (
        f"Validating token “{word.text}” on merchant {merchant_name}, "
        f"receipt {word.receipt_id}, line {word.line_id}, word {word.word_id}; "
        f"proposed label: {label_text}.\n"
    )
    # Include the allowed labels for reference
    prompt += "Allowed labels: " + ", ".join(CORE_LABELS) + "\n\n"
    # prompt += f"Context: “{context_str}”\n\n"
    # if ctx_neighbors:
    #     prompt += f"Top {len(ctx_neighbors)} full-line examples:\n"
    #     for i, m in enumerate(ctx_neighbors, 1):
    #         prompt += f"{i}. {m.metadata['text']} (label: {m.metadata['label']}) — score {m.score:.4f}\n"
    # else:
    #     prompt += "No similar full-line examples found.\n"
    # if word_neighbors:
    #     prompt += f"\nTop {len(word_neighbors)} token-level examples:\n"
    #     for i, m in enumerate(word_neighbors, 1):
    #         prompt += f"{i}. {m.metadata['text']} (label: {m.metadata['label']}) — score {m.score:.4f}\n"
    # else:
    #     prompt += "\nNo similar token-level examples found.\n"
    prompt += (
        "If the proposed label is correct, return is_valid = true.\n"
        "If it is incorrect and you are confident about a better label from the allowed list, return is_valid = false with correct_label and rationale.\n"
        "If you are not confident in any better label, return is_valid = false only (leave correct_label and rationale blank).\n"
    )
    return prompt


def main():
    image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
    receipt_id = 1

    # Get the receipt details
    (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    receipt_metadata = dynamo_client.getReceiptMetadata(image_id, receipt_id)
    merchant_name = receipt_metadata.merchant_name

    # Filter for ADDRESS_LINE labels
    address_line_labels = [lbl for lbl in labels if lbl.label == "ADDRESS_LINE"]

    # Map words by their (line_id, word_id)
    word_map = {(w.line_id, w.word_id): w for w in words}

    # Collect all words matching those ADDRESS_LINE labels
    receipt_words = [
        word_map[(lbl.line_id, lbl.word_id)]
        for lbl in labels
        if (lbl.line_id, lbl.word_id) in word_map
    ]

    for word in receipt_words:
        # Determine the label for this word
        matching_label = next(
            (
                lbl
                for lbl in labels
                if lbl.line_id == word.line_id and lbl.word_id == word.word_id
            ),
            None,
        )
        label_text = matching_label.label if matching_label else "UNKNOWN"

        # Build a hybrid context window using get_hybrid_context
        # context_words = get_hybrid_context(word, words)
        # context = " ".join(w.text for w in context_words)

        # Build word-level input string for embedding
        # centroid = word.calculate_centroid()
        # x_center, y_center = centroid
        # input_text = (
        #     f"{word.text} [label={label_text}] "
        #     f"(merchant={merchant_name}) "
        #     f"(pos={x_center:.4f},{y_center:.4f}) "
        #     f"angle={word.angle_degrees:.2f} "
        #     f"conf={word.confidence:.2f}"
        # )

        # Precompute embeddings
        # word_embedding = get_embedding(input_text)
        # context_embedding = get_embedding(context)

        # # Query Pinecone for context-view neighbors
        # ctx_neighbors = fetch_neighbors(context_embedding, merchant_name, "context", 5)
        # word_neighbors = fetch_neighbors(word_embedding, merchant_name, "word", 5)

        # Deduplicate and shorten neighbor lists to top 3 unique examples
        # def dedupe_and_shorten(neighbors, max_items=3):
        #     seen = set()
        #     unique = []
        #     for m in neighbors:
        #         text = m.metadata["text"]
        #         if text not in seen:
        #             seen.add(text)
        #             unique.append(m)
        #         if len(unique) >= max_items:
        #             break
        #     return unique

        # ctx_neighbors = dedupe_and_shorten(ctx_neighbors, max_items=3)
        # word_neighbors = dedupe_and_shorten(word_neighbors, max_items=3)

        prompt = format_prompt(
            word,
            # context,
            # ctx_neighbors,
            # word_neighbors,
            label_text,
            merchant_name,
            # receipt_id,
        )

        # --- Validate via ChatGPT function call ---
        validate_fn = {
            "name": "validate_label",
            "description": "Decide whether a token's proposed label is correct, and if not, suggest the correct label with a brief rationale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_valid": {
                        "type": "boolean",
                        "description": "True if the proposed label is correct for the token, else False.",
                    },
                    "correct_label": {
                        "type": "string",
                        "description": "If is_valid is False, the label that should have been assigned.",
                        "enum": CORE_LABELS,
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-sentence explanation of why this label is (in)correct.",
                    },
                },
                "required": ["is_valid"],
                "if": {
                    "properties": {"is_valid": {"const": False}},
                },
                "then": {
                    "required": [],
                },
            },
        }
        # Call ChatGPT with function specification
        resp = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            functions=[validate_fn],
            function_call={"name": "validate_label"},
        )
        # Parse and print the structured validation result
        fn_call = resp.choices[0].message.function_call
        raw_args = fn_call.arguments
        try:
            result = json.loads(raw_args)
        except json.JSONDecodeError:
            import ast

            # Fallback to Python literal parsing if JSON is invalid
            result = ast.literal_eval(raw_args)
        print("Validation result:")
        print(f"  word          = {word.text}")
        print(f"  is_valid      = {result['is_valid']}")
        print(f"  is_valid      = {result['is_valid']}")
        if not result["is_valid"]:
            if "correct_label" in result:
                print(f"  correct_label = {result['correct_label']}")
            if "rationale" in result:
                print(f"  rationale     = {result['rationale']}")


if __name__ == "__main__":
    main()

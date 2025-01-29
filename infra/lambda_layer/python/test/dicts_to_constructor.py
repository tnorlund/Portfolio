import os
from dynamo import DynamoClient


def dicts_to_constructors(
    obj_type: str,
    list_of_dicts: list[dict],
    timestamp_added="2021-01-01T00:00:00.000000",
    raw_s3_bucket="raw-image-bucket",
    cdn_s3_bucket="sitebucket",
) -> list[str]:
    """
    Given an object type (class name) and a list of dictionaries, each dict representing
    the parameters for a single constructor call, return a list of single-line constructor calls.

    If a dictionary has a 'timestamp_added' key, it is replaced with a fixed string
    (e.g. "2021-01-01T00:00:00").
    """
    calls = []
    for original_params in list_of_dicts:
        # Make a copy so we don't modify the original dict
        params = original_params.copy()

        # If 'timestamp_added' is in the dict, override it
        if "timestamp_added" in params:
            params["timestamp_added"] = timestamp_added
        if "raw_s3_bucket" in params:
            params["raw_s3_bucket"] = raw_s3_bucket
        if "cdn_s3_bucket" in params:
            params["cdn_s3_bucket"] = cdn_s3_bucket

        # Build "key=value" substrings using repr() for correct Python literals
        pair_strs = [f"{key}={repr(value)}" for key, value in params.items()]
        joined = ", ".join(pair_strs)

        calls.append(f"{obj_type}({joined})")

    return calls


if __name__ == "__main__":
    # 1) Grab data from Dynamo or wherever
    dynamo_client = DynamoClient(os.getenv("DYNAMO_DB_TABLE"))  # Adjust as needed

    # For example, get an image and lines from your DB:
    image_details = dynamo_client.getImageDetails(image_id=1)
    image, lines, words, letters, receipt_details = image_details

    receipts = []
    receipt_lines = []
    receipt_words = []
    receipt_letters = []

    for receipt_detail_dict in receipt_details:
        receipts.append(receipt_detail_dict["receipt"])
        receipt_lines.extend(receipt_detail_dict["lines"])
        receipt_words.extend(receipt_detail_dict["words"])
        receipt_letters.extend(receipt_detail_dict["letters"])



    # 2) Convert the data to constructor calls:
    image_constructor = dicts_to_constructors(
        "Image",
        [
            {
                key: value
                for key, value in dict(image).items()
            }
        ],
    )

    lines_constructors = dicts_to_constructors(
        "Line",
        [
            {
                key: value
                for key, value in dict(line).items()
                if key not in ("num_chars", "histogram")
            }
            for line in lines
        ],
    )

    words_constructors = dicts_to_constructors(
        "Word",
        [
            {
                key: value
                for key, value in dict(word).items()
                if key not in ("num_chars", "histogram")
            }
            for word in words
        ],
    )

    letters_constructors = dicts_to_constructors(
        "Letter",
        [
            {
                key: value
                for key, value in dict(letter).items()
                if key not in ("num_chars", "histogram")
            }
            for letter in letters
        ],
    )

    receipt_constructors = dicts_to_constructors(
        "Receipt",
        [
            {
                key: value
                for key, value in dict(receipt).items()
            }
            for receipt in receipts
        ],
    )

    receipt_line_constructors = dicts_to_constructors(
        "ReceiptLine",
        [
            {
                key: value
                for key, value in dict(receipt_line).items()
                if key not in ("num_chars", "histogram")
            }
            for receipt_line in receipt_lines
        ],
    )

    receipt_word_constructors = dicts_to_constructors(
        "ReceiptWord",
        [
            {
                key: value
                for key, value in dict(receipt_word).items()
                if key not in ("num_chars", "histogram")
            }
            for receipt_word in receipt_words
        ],
    )

    receipt_letter_constructors = dicts_to_constructors(
        "ReceiptLetter",
        [
            {
                key: value
                for key, value in dict(receipt_letter).items()
                if key not in ("num_chars", "histogram")
            }
            for receipt_letter in receipt_letters
        ],
    )

    # 3) Write them to a file (replicating the same style you had on-screen)
    output_file = "generated_constructors.txt"
    with open(output_file, "w") as f:
        # The 'image_constructor' list has one element, so we use [0]
        f.write(f"        {image_constructor[0]},\n")
        f.write("        [\n")
        for line_constructor in lines_constructors:
            f.write(f"            {line_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for word_constructor in words_constructors:
            f.write(f"            {word_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for letter_constructor in letters_constructors:
            f.write(f"            {letter_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for receipt_constructor in receipt_constructors:
            f.write(f"            {receipt_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for receipt_line_constructor in receipt_line_constructors:
            f.write(f"            {receipt_line_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for receipt_word_constructor in receipt_word_constructors:
            f.write(f"            {receipt_word_constructor},\n")
        f.write("        ],\n")
        f.write("        [\n")
        for receipt_letter_constructor in receipt_letter_constructors:
            f.write(f"            {receipt_letter_constructor},\n")
        f.write("        ],\n")


    print(f"Wrote constructors to {output_file}")
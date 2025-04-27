import json
from receipt_dynamo.entities import ReceiptWord, ReceiptLine
import random

from receipt_label.submit_completion_batch import (
    list_labels_that_need_validation,
    chunk_into_completion_batches,
    serialize_labels,
    upload_serialized_labels,
    generate_completion_batch_id,
    format_batch_completion_file,
    upload_to_openai,
    submit_openai_batch,
    get_receipt_details,
    download_serialized_labels,
    deserialize_labels,
    create_batch_summary,
    add_batch_summary,
    update_label_validation_status,
)
from receipt_label.utils import get_clients
from collections import Counter
import logging
import os

S3_BUCKET = os.environ["S3_BUCKET"]

dynamo_client, openai_client, pinecone_index = get_clients()

print("Getting labels that need validation")
labels_that_need_validation = list_labels_that_need_validation()
print(f"Found {len(labels_that_need_validation)} labels that need validation")
print("Chunking labels into completion batches")
chunks = chunk_into_completion_batches(labels_that_need_validation)

image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1
new_chunks = {}
new_chunks[image_id] = {}
new_chunks[image_id][receipt_id] = chunks[image_id][receipt_id]

serialized_labels = serialize_labels(new_chunks)

print("Uploading serialized labels to S3")
results = upload_serialized_labels(serialized_labels, S3_BUCKET)
print(results)
for event in results:
    print(event)
    serialized_labels_file = download_serialized_labels(event)
    deserialized_labels = deserialize_labels(serialized_labels_file)
    # Get unique image_id and receipt_id from deserialized_labels
    image_id = deserialized_labels[0].image_id
    receipt_id = deserialized_labels[0].receipt_id
    # Get receipt details
    lines, words, metadata = get_receipt_details(image_id, receipt_id)
    # Format batch completion file
    filepath = format_batch_completion_file(lines, words, deserialized_labels, metadata)
    file_object = upload_to_openai(filepath)
    batch = submit_openai_batch(file_object.id)
    print(f"Batch {batch.id} submitted")
    # Generate an internal batch_id for our own tracking, and use the OpenAI batch's
    # `id` field as the open_ai_batch_id recorded in BatchSummary.
    internal_batch_id = generate_completion_batch_id()
    batch_summary = create_batch_summary(internal_batch_id, batch.id, str(filepath))
    add_batch_summary(batch_summary)
    update_label_validation_status(deserialized_labels)
    print(f"Batch {batch.id} submitted and DynamoDB updated")

# for image_id, receipt_id in new_chunks:
#     lines, words, metadata = get_receipt_details(image_id, receipt_id)
# lines, words, metadata = get_receipt_details(image_id, receipt_id)

# (
#     _,
#     _,
#     _,
#     _,
#     _,
#     labels,
# ) = dynamo_client.getReceiptDetails(image_id, receipt_id)

# filepath = format_batch_completion_file(lines, words, labels, metadata)
# # print(filepath)
# file_object = upload_to_openai(filepath)
# batch = submit_openai_batch(file_object.id)

# # Pick a random word from the receipt
# label = random.choice(labels)
# word = next(
#     w for w in words if w.line_id == label.line_id and w.word_id == label.word_id
# )
# print(f"Validating label: {label.label} for word: {word.text}")


# def _prompt_receipt_text(word: ReceiptWord, lines: list[ReceiptLine]) -> str:
#     """Format the receipt text for the prompt."""
#     if word.line_id == lines[0].line_id:
#         prompt_receipt = lines[0].text
#         prompt_receipt = prompt_receipt.replace(
#             word.text, f"<TARGET>{word.text}</TARGET>"
#         )
#     else:
#         prompt_receipt = lines[0].text

#     for index in range(1, len(lines)):
#         previous_line = lines[index - 1]
#         current_line = lines[index]
#         if current_line.line_id == word.line_id:
#             # Replace the word in the line text with <TARGET>text</TARGET>
#             line_text = current_line.text
#             line_text = line_text.replace(word.text, f"<TARGET>{word.text}</TARGET>")
#         else:
#             line_text = current_line.text
#         current_line_centroid = current_line.calculate_centroid()
#         if (
#             current_line_centroid[1] < previous_line.top_left["y"]
#             and current_line_centroid[1] > previous_line.bottom_left["y"]
#         ):
#             prompt_receipt += f" {line_text}"
#         else:
#             prompt_receipt += f"\n{line_text}"
#     return prompt_receipt


# def validation_prompt(word: ReceiptWord, lines: list[ReceiptLine]) -> str:
#     prompt = f'You are confirming the label for the word: {word.text} on the receipt from "{metadata.merchant_name}"'
#     prompt += (
#         f'\nThis "{metadata.merchant_name}" location is located at {metadata.address}'
#     )
#     prompt += f"\nI've marked the word with <TARGET>...</TARGET>"
#     prompt += f"\nThe receipt is as follows:"
#     prompt += f"\n--------------------------------"
#     prompt += _prompt_receipt_text(word, lines)
#     prompt += f"\n--------------------------------"
#     prompt += f"\nThe label you are confirming is: {label.label}"
#     prompt += f'\nThe allowed labels are: {", ".join(CORE_LABELS)}'
#     prompt += f"\nIf the label is correct, return is_valid = true."
#     prompt += f"\nIf it is incorrect and you are confident in a better label from the allowed list, return is_valid = false and return correct_label and rationale."
#     prompt += f"\nIf you are not confident in any better label from the allowed list, return is_valid = false only. Do not guess. Leave correct_label and rationale blank."
#     return prompt


# resp = openai_client.chat.completions.create(
#     model="gpt-3.5-turbo",
#     messages=[{"role": "user", "content": validation_prompt(word, lines)}],
#     functions=[
#         {
#             "name": "validate_label",
#             "description": "Decide whether a token's proposed label is correct, and if not, suggest the correct label with a brief rationale.",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "is_valid": {
#                         "type": "boolean",
#                         "description": "True if the proposed label is correct for the token, else False.",
#                     },
#                     "correct_label": {
#                         "type": "string",
#                         "description": "Only return if is_valid is false and you are confident the word should be labeled with one of the allowed labels. Do not return this if unsure.",
#                         "enum": CORE_LABELS,
#                     },
#                     "rationale": {
#                         "type": "string",
#                         "description": "Only return if is_valid is false and you are confident in the correct label. Explain briefly why the word fits the suggested label.",
#                     },
#                 },
#                 "required": ["is_valid"],
#                 "if": {
#                     "properties": {"is_valid": {"const": False}},
#                 },
#                 "then": {
#                     "required": [],
#                 },
#             },
#         }
#     ],
#     function_call={"name": "validate_label"},
# )
# result = resp.choices[0].message.function_call.arguments
# try:
#     result = json.loads(result)
# except json.JSONDecodeError:
#     import ast

#     # Fallback to Python literal parsing if JSON is invalid
#     result = ast.literal_eval(result)
# # Sanitize and validate model-suggested label
# if not result["is_valid"]:
#     suggested = result.get("correct_label")
#     if suggested not in CORE_LABELS:
#         print(f"Got bad label from model: {suggested}")
#         result.pop("correct_label", None)
#         result.pop("rationale", None)
# # print(_prompt_receipt_text(word, lines))
# print()
# print("Result:")
# print(f"  word          = {word.text}")
# print(f"  label         = {label.label}")
# print(f"  is_valid      = {result['is_valid']}")
# # if not result["is_valid"] and result.get("correct_label") and result.get("rationale"):
# print(f"  correct_label = {result['correct_label']}")
# print(f"  rationale     = {result['rationale']}")
# # else:
# #     print("Got bad result from model")

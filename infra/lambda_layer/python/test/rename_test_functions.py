import os
import re

# STEP 1: Build your old->new mapping from the suggestions above.
RENAME_MAP = {
    "unit/test_receipt_line.py": {
        "test_receipt_line_valid_init": "test_receipt_line_init_valid",
        "test_invalid_receipt_id": "test_receipt_line_init_invalid_receipt_id",
        "test_invalid_image_id": "test_receipt_line_init_invalid_image_id",
        "test_invalid_id": "test_receipt_line_init_invalid_id",
        "test_invalid_text": "test_receipt_line_init_invalid_text",
        "test_invalid_angles": "test_receipt_line_init_invalid_angles",
        "test_invalid_confidence": "test_receipt_line_init_invalid_confidence",
        "test_equal_receipt_line": "test_receipt_line_eq",
        "test_repr": "test_receipt_line_repr",
        "test_iter": "test_receipt_line_iter",
        "test_itemToReceiptLine": "test_item_to_receipt_line",
    },
    "unit/test_letter.py": {
        "test_init": "test_letter_init_valid",
        "test_init_bad_uuid": "test_letter_init_invalid_uuid",
        "test_init_invalid_line_id": "test_letter_init_invalid_line_id",
        "test_init_invalid_word_id": "test_letter_init_invalid_word_id",
        "test_init_invalid_id": "test_letter_init_invalid_id",
        "test_init_invalid_text": "test_letter_init_invalid_text",
        "test_init_invalid_bounding_box": "test_letter_init_invalid_bounding_box",
        "test_init_invalid_top_right": "test_letter_init_invalid_top_right",
        "test_init_invalid_top_left": "test_letter_init_invalid_top_left",
        "test_init_invalid_bottom_right": "test_letter_init_invalid_bottom_right",
        "test_init_invalid_bottom_left": "test_letter_init_invalid_bottom_left",
        "test_init_invalid_confidence": "test_letter_init_invalid_confidence",
        "test_init_invalid_angels": "test_letter_init_invalid_angles",
        "test_key": "test_letter_key",
        "test_to_item": "test_letter_to_item",
        "test_calculate_centroid": "test_letter_calculate_centroid",
        "test_shear": "test_letter_shear",
        "test_warp_affine": "test_letter_warp_affine",
        "test_repr": "test_letter_repr",
        "test_iter": "test_letter_iter",
        "test_eq": "test_letter_eq",
        "test_itemToLetter": "test_item_to_letter",
    },
    "unit/test_receipt_word_tag.py": {
        "test_receipt_word_tag_init": "test_receipt_word_tag_init_valid",
        "test_receipt_word_tag_init_bad_image_id": "test_receipt_word_tag_init_invalid_image_id",
        "test_receipt_word_tag_init_bad_receipt_id": "test_receipt_word_tag_init_invalid_receipt_id",
        "test_receipt_word_tag_init_bad_line_id": "test_receipt_word_tag_init_invalid_line_id",
        "test_receipt_word_tag_init_bad_word_id": "test_receipt_word_tag_init_invalid_word_id",
        "test_receipt_word_tag_init_bad_tag": "test_receipt_word_tag_init_invalid_tag",
        "test_receipt_word_tag_init_bad_timestamp_added": "test_receipt_word_tag_init_invalid_timestamp_added",
        "test_to_ReceiptWord_key": "test_receipt_word_tag_to_receipt_word_key",
        "test_item_to_receipt_word_tag_bad_format": "test_item_to_receipt_word_tag_invalid_format",
    },
    "unit/test_gpt_tagging.py": {
        "test_gpt_tagging_init": "test_gpt_tagging_init_valid",
        "test_gpt_tagging_init_bad_image_id": "test_gpt_tagging_init_invalid_image_id",
        "test_gpt_tagging_init_bad_receipt_id": "test_gpt_tagging_init_invalid_receipt_id",
        "test_gpt_tagging_init_bad_query": "test_gpt_tagging_init_invalid_query",
        "test_gpt_tagging_init_bad_response": "test_gpt_tagging_init_invalid_response",
        "test_gpt_tagging_init_bad_timestamp": "test_gpt_tagging_init_invalid_timestamp",
        "test_item_to_gpt_tagging_bad_format": "test_item_to_gpt_tagging_invalid_format",
    },
    "unit/test_receipt_letter.py": {
        "test_init": "test_receipt_letter_init_valid",
        "test_init_invalid_receipt_id": "test_receipt_letter_init_invalid_receipt_id",
        "test_init_invalid_uuid": "test_receipt_letter_init_invalid_uuid",
        "test_init_invalid_line_id": "test_receipt_letter_init_invalid_line_id",
        "test_init_invalid_word_id": "test_receipt_letter_init_invalid_word_id",
        "test_init_invalid_id": "test_receipt_letter_init_invalid_id",
        "test_init_invalid_text": "test_receipt_letter_init_invalid_text",
        "test_init_invalid_bounding_box": "test_receipt_letter_init_invalid_bounding_box",
        "test_init_invalid_top_right": "test_receipt_letter_init_invalid_top_right",
        "test_init_invalid_top_left": "test_receipt_letter_init_invalid_top_left",
        "test_init_invalid_bottom_right": "test_receipt_letter_init_invalid_bottom_right",
        "test_init_invalid_bottom_left": "test_receipt_letter_init_invalid_bottom_left",
        "test_init_invalid_angles": "test_receipt_letter_init_invalid_angles",
        "test_receipt_letter_invalid_confidence": "test_receipt_letter_init_invalid_confidence",
        "test_itemToWord": "test_item_to_word",
    },
    "unit/test_line.py": {
        "test_init": "test_line_init_valid",
        "test_init_bad_uuid": "test_line_init_invalid_uuid",
        "test_init_bad_id": "test_line_init_invalid_id",
        "test_init_bad_text": "test_line_init_invalid_text",
        "test_init_bad_bounding_box": "test_line_init_invalid_bounding_box",
        "test_init_bad_top_left": "test_line_init_invalid_top_left",
        "test_init_bad_top_right": "test_line_init_invalid_top_right",
        "test_init_bad_bottom_left": "test_line_init_invalid_bottom_left",
        "test_init_bad_bottom_right": "test_line_init_invalid_bottom_right",
        "test_init_bad_angle": "test_line_init_invalid_angle",
        "test_init_bad_confidence": "test_line_init_invalid_confidence",
        "test_key": "test_line_key",
        "test_gsi1_key": "test_line_gsi1_key",
        "test_to_item": "test_line_to_item",
        "test_calculate_centroid": "test_line_calculate_centroid",
        "test_translate": "test_line_translate",
        "test_scale": "test_line_scale",
        "test_rotate_limited_range": "test_line_rotate_limited_range",
        "test_shear": "test_line_shear",
        "test_warp_affine": "test_line_warp_affine",
        "test_repr": "test_line_repr",
        "test_iter": "test_line_iter",
        "test_eq": "test_line_eq",
        "test_itemToLine": "test_item_to_line",
    },
    "unit/test_image.py": {
        "test_init": "test_image_init_valid",
        "test_init_bad_id": "test_image_init_invalid_id",
        "test_init_bad_width_and_height": "test_image_init_invalid_width_and_height",
        "test_init_bad_timestamp": "test_image_init_invalid_timestamp",
        "test_init_bad_s3_bucket": "test_image_init_invalid_s3_bucket",
        "test_init_bad_s3_key": "test_image_init_invalid_s3_key",
        "test_init_bad_sha256": "test_image_init_invalid_sha256",
        "test_init_bad_cdn_s3_bucket": "test_image_init_invalid_cdn_s3_bucket",
        "test_init_bad_cdn_s3_key": "test_image_init_invalid_cdn_s3_key",
        "test_key": "test_image_key",
        "test_gsi1_key": "test_image_gsi1_key",
        "test_to_item": "test_image_to_item",
        "test_to_item_no_sha": "test_image_to_item_no_sha",
        "test_to_item_no_cdn_bucket": "test_image_to_item_no_cdn_bucket",
        "test_to_item_no_cdn_key": "test_image_to_item_no_cdn_key",
        "test_repr": "test_image_repr",
        "test_iter": "test_image_iter",
        "test_eq": "test_image_eq",
        "test_itemToImage": "test_item_to_image",
    },
    "unit/test_word_tag.py": {
        "test_word_tag_init": "test_word_tag_init_valid",
        "test_word_tag_init_bad_image_id": "test_word_tag_init_invalid_image_id",
        "test_word_tag_init_bad_line_id": "test_word_tag_init_invalid_line_id",
        "test_word_tag_init_bad_word_id": "test_word_tag_init_invalid_word_id",
        "test_word_tag_init_bad_tag": "test_word_tag_init_invalid_tag",
        "test_word_tag_init_bad_timestamp_added": "test_word_tag_init_invalid_timestamp_added",
        "test_to_Word_key": "test_word_tag_to_word_key",
        "test_item_to_word_tag_bad_format": "test_item_to_word_tag_invalid_format",
    },
    "unit/test_receipt.py": {
        "test_init": "test_receipt_init_valid",
        "test_receipt_invalid_image_id": "test_receipt_init_invalid_image_id",
        "test_receipt_invalid_id": "test_receipt_init_invalid_id",
        "test_receipt_invalid_dimensions": "test_receipt_init_invalid_dimensions",
        "test_valid_timestamp": "test_receipt_init_valid_timestamp",
        "test_receipt_invalid_timestamp": "test_receipt_init_invalid_timestamp",
        "test_receipt_invalid_s3_bucket": "test_receipt_init_invalid_s3_bucket",
        "test_receipt_invalid_s3_key": "test_receipt_init_invalid_s3_key",
        "test_receipt_invalid_point_types": "test_receipt_init_invalid_point_types",
        "test_receipt_invalid_sha256": "test_receipt_init_invalid_sha256",
        "test_receipt_invalid_cdn_bucket": "test_receipt_init_invalid_cdn_bucket",
        "test_receipt_invalid_cdn_key": "test_receipt_init_invalid_cdn_key",
        "test_key_generation": "test_receipt_key_generation",
        "test_gsi1_key_generation": "test_receipt_gsi1_key_generation",
        "test_gsi2_key_generation": "test_receipt_gsi2_key_generation",
        "test_to_item": "test_receipt_to_item",
        "test_repr": "test_receipt_repr",
        "test_iter": "test_receipt_iter",
        "test_eq": "test_receipt_eq",
        "test_item_to_receipt_valid": "test_item_to_receipt_valid_input",
    },
    "unit/test_gpt_validation.py": {
        "test_gpt_validation_init": "test_gpt_validation_init_valid",
        "test_gpt_validation_init_bad_image_id": "test_gpt_validation_init_invalid_image_id",
        "test_gpt_validation_init_bad_receipt_id": "test_gpt_validation_init_invalid_receipt_id",
        "test_gpt_validation_init_bad_line_id": "test_gpt_validation_init_invalid_line_id",
        "test_gpt_validation_init_bad_word_id": "test_gpt_validation_init_invalid_word_id",
        "test_gpt_validation_init_bad_tag": "test_gpt_validation_init_invalid_tag",
        "test_gpt_validation_init_bad_query": "test_gpt_validation_init_invalid_query",
        "test_gpt_validation_init_bad_response": "test_gpt_validation_init_invalid_response",
        "test_gpt_validation_init_bad_timestamp": "test_gpt_validation_init_invalid_timestamp",
        "test_item_to_gpt_validation_bad_format": "test_item_to_gpt_validation_invalid_format",
    },
    "unit/test_receipt_word.py": {
        "test_receipt_word_valid_init": "test_receipt_word_init_valid",
        "test_invalid_receipt_id": "test_receipt_word_init_invalid_receipt_id",
        "test_invalid_uuid": "test_receipt_word_init_invalid_uuid",
        "test_invalid_line_id": "test_receipt_word_init_invalid_line_id",
        "test_invalid_id": "test_receipt_word_init_invalid_id",
        "test_invalid_text": "test_receipt_word_init_invalid_text",
        "test_receipt_word_bounding_box_validation": "test_receipt_word_init_invalid_bounding_box",
        "test_corners": "test_receipt_word_corners",
        "test_angle_validation": "test_receipt_word_angle_validation",
        "test_receipt_word_confidence_validation": "test_receipt_word_init_invalid_confidence",
        "test_invalid_tags": "test_receipt_word_init_invalid_tags",
        "test_equal_receipt_word": "test_receipt_word_eq",
        "test_iter": "test_receipt_word_iter",
        "test_calculate_centroid": "test_receipt_word_calculate_centroid",
        "test_distance_and_angle_from_ReceiptWord": "test_receipt_word_distance_and_angle",
    },
    "unit/test_word.py": {
        "test_init": "test_word_init_valid",
        "test_init_bad_uuid": "test_word_init_invalid_uuid",
        "test_init_bad_line_id": "test_word_init_invalid_line_id",
        "test_init_bad_id": "test_word_init_invalid_id",
        "test_init_bad_text": "test_word_init_invalid_text",
        "test_init_bad_bounding_box": "test_word_init_invalid_bounding_box",
        "test_init_bad_corners": "test_word_init_invalid_corners",
        "test_init_bad_angle": "test_word_init_invalid_angle",
        "test_init_bad_confidence": "test_word_init_invalid_confidence",
        "test_init_bad_tags": "test_word_init_invalid_tags",
        "test_key": "test_word_key",
        "test_to_item": "test_word_to_item",
        "test_calculate_centroid": "test_word_calculate_centroid",
        "test_shear": "test_word_shear",
        "test_warp_affine": "test_word_warp_affine",
        "test_repr": "test_word_repr",
        "test_iter": "test_word_iter",
        "test_eq": "test_word_eq",
        "test_itemToWord": "test_item_to_word",
    },
}

def rename_test_functions_in_file(file_path: str):
    # Retrieve the mapping for this file, if any
    mapping = RENAME_MAP.get(file_path)
    if not mapping:
        return  # No renames specified for this file

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed = False
    new_lines = []

    # Build regex using the keys from the file-specific mapping (old function names)
    pattern = re.compile(r'^(\s*)def\s+(' + '|'.join(map(re.escape, mapping.keys())) + r')(\s*\(.*)?:')

    for line in lines:
        match = pattern.match(line)
        if match:
            indent = match.group(1)
            old_def_name = match.group(2)
            remainder = match.group(3) if match.group(3) else "():"
            new_def_name = mapping[old_def_name]
            # Rebuild the line with the new function name
            new_line = f"{indent}def {new_def_name}{remainder}:\n"
            new_lines.append(new_line)
            changed = True
        else:
            new_lines.append(line)

    if changed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

def rename_test_functions_in_dir(base_dir: str):
    for root, dirs, files in os.walk(base_dir):
        for filename in files:
            if filename.endswith(".py"):
                file_path = os.path.join(root, filename)
                rename_test_functions_in_file(file_path)

if __name__ == "__main__":
    unit_test_dir = "unit"  # or wherever your test files actually reside
    rename_test_functions_in_dir(unit_test_dir)
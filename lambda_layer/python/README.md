# Python Lambda Layer

This is a Python Lambda Layer that helps with accessing DynamoDB for receipt data.

## Table Design

| Item Type                | PK                 | SK                                                                    | GSI PK            | GSI SK                                   | Attributes                                                                                                             |
|:-------------------------|:-------------------|:----------------------------------------------------------------------|:------------------|:-----------------------------------------|:-----------------------------------------------------------------------------------------------------------------------|
| **Image**                | `IMAGE#<image_id>` | `IMAGE`                                                               | `IMAGE`           | `IMAGE#<image_id>`                       | `width`, `height`, `timestamp_added`, `s3_bucket`, `s3_key`, `sha256`                                                  |
| **Line**                 | `IMAGE#<image_id>` | `LINE#<line_id>`                                                      | `IMAGE`           | `IMAGE#<image_id>#LINE#<line_id>`        | `text`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence`         |
| **Word**                 | `IMAGE#<image_id>` | `LINE#<line_id>WORD#<word_id>`                                        |                   |                                          | `text`, `tags`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence` |
| **Word Tag**             | `IMAGE#<image_id>` | `TAG#<tag>#WORD#<word_id>`                                            | `TAG#<UPPER_TAG>` | `WORD#<word_id>`                         | `tag_name`                                                                                                             |
| **Letter**               | `IMAGE#<image_id>` | `LINE#<line_id>WORD#<word_id>LETTER#<letter_id>`                      |                   |                                          | `text`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence`         |
| **Receipt**              | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>`                                                | `IMAGE`           | `IMAGE#<image_id>#RECEIPT#<receipt_id>`  | `width`, `height`, `timestamp_added`, `s3_bucket`, `s3_key`, `top_left`, `bottom_right`, `bottom_left`, `sha256`       |
| **Receipt Line**         | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>#LINE#<line_id>`                                 |                   |                                          | `text`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence`         |
| **Receipt Word**         | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>#LINE#<line_id>WORD#<word_id>`                   |                   |                                          | `text`, `tags`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence` |
| **Receipt Word Tag**     | `IMAGE#<image_id>` | `TAG#<tag>#RECEIPT#<receipt_id>#WORD#<word_id>`                       | `TAG#<UPPER_TAG>` | `RECEIPT#<receipt_id>#WORD#<word_id>`    | `tag_name`                                                                                                             |
| **Receipt Letter**       | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>#LINE#<line_id>WORD#<word_id>LETTER#<letter_id>` |                   |                                          | `text`, `top_right`, `top_left`, `bottom_right`, `bottom_left`, `angle_degrees`, `angle_radians`, `confidence`         |

## Key Design Notes

1. **Unified Partition Key**:

   - All items related to an image (lines, words, word tags, receipts) share the same partition key (`PK = IMAGE#<image_id>`).

2. **Uppercased Tags**:

   - All tags are stored in **UPPERCASE** to enforce consistency and avoid issues with case sensitivity.
   - Example:
     - Word Tag: `TAG#FOOD#WORD#1`
     - Receipt-Word Tag: `TAG#FOOD#RECEIPT#1#WORD#1`

3. **Embedded Tags**:

   - The `Word` and `Receipt-Word` tables include a `tags` attribute, which is a list of all tags (in UPPERCASE) associated with them.
   - Example:
     - Word: `tags: ["FOOD", "DAIRY"]`
     - Receipt-Word: `tags: ["GROCERY", "DISCOUNTED"]`

4. **Scalable Many-to-Many Relationships**:
   - A tag can apply to multiple words or receipt-words, and a word or receipt-word can have multiple tags.

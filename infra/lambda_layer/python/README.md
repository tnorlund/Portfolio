# Python Lambda Layer

This is a Python Lambda Layer that helps with accessing DynamoDB for receipt data.

## Table Design

| Item Type                | PK                         | SK                                                                                       | GSI1 PK      | GSI1 SK                                                                         | GSI2 PK   | GSI2 SK                                                                         | Attributes                                                                                                             |
|:-------------------------|:---------------------------|:-----------------------------------------------------------------------------------------|:-------------|:--------------------------------------------------------------------------------|:----------|:--------------------------------------------------------------------------------|:-----------------------------------------------------------------------------------------------------------------------|
| **Image**                | `IMAGE#<image_id>`         | `IMAGE`                                                                                  | `IMAGE`      | `IMAGE#<image_id>`                                                              |           |                                                                                 | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `sha256`                      |
| **Line**                 | `IMAGE#<image_id>`         | `LINE#<line_id>`                                                                         | `IMAGE`      | `IMAGE#<image_id>#LINE#<line_id>`                                               |           |                                                                                 | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Word**                 | `IMAGE#<image_id>`         | `LINE#<line_id>#WORD#<word_id>`                                                          |              |                                                                                 |           |                                                                                 | - `text` <br>- `tags` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Word Tag**             | `IMAGE#<image_id>`         | `LINE#<line_id>#WORD#<word_id>#TAG#<tag>`                                                | `TAG#<tag>`  | `IMAGE#<image_id>#LINE#<line_id>#WORD#<word_id>`                                |           |                                                                                 | - `tag_name` <br>- `timestamp_added`                                                                                    |
| **Letter**               | `IMAGE#<image_id>`         | `LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>`                                       |              |                                                                                 |           |                                                                                 | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Receipt**              | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>`                                                                   | `IMAGE`      | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         | `RECEIPT` | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `sha256`       |
| **Receipt Line**         | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>`                                                    |              |                                                                                 |           |                                                                                 | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`         |
| **Receipt Word**         | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`                                     |              |                                                                                 | `RECEIPT` | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`           | - `text` <br>- `tags` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Receipt Word Tag**     | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>`                           | `TAG#<tag>`  | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>` | `RECEIPT` | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>` | - `tag_name` <br>- `timestamp_added`                                                                                    |
| **Receipt Letter**       | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>`                  |              |                                                                                 |           |                                                                                 | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **GPT Validation**       | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>#QUERY#VALIDATION`          |              |                                                                                 |           |                                                                                 | - `query` <br>- `response` <br>- `timestamp_added`                                                                     |
| **GPT Initial Tagging**  | `IMAGE#<image_id>`         | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>#QUERY#INITIAL_TAGGING`     |              |                                                                                 |           |                                                                                 | - `query` <br>- `response` <br>- `timestamp_added`                                                                     |

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

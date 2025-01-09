# Python Lambda Layer

This is a Python Lambda Layer that helps with accessing DynamoDB for receipt data.

## Table Design

| Item Type | PK | SK | Attributes |
| --- | --- | --- | --- |
| Image | `IMAGE#<image_id>` | `#IMAGE` | width, height, timestampAdded, s3Bucket, s3Key |
| Line | `IMAGE#<image_id>` | `LINE#<line_id>` | text, x, y, width, height, angle, confidence |
| Word | `IMAGE#<image_id>` | `LINE#<line_id>WORD#<word_id>` | text, x, y, width, height, angle, confidence |
| Letter | `IMAGE#<image_id>` | `LINE#<line_id>WORD#<word_id>LETTER#<letter_id>` | text, x, y, width, height, angle, confidence |
| Scaled Image | `IMAGE#<image_id>` | `#SCALED#<quality>` |  timestampAdded, base64, quality |
| Receipt | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>` | timestampAdded |

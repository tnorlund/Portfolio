# example

This project will read receipts and extract the information from them.

1. Upload the image to S3
2. Use OCR to grab letters and convert to Base64
3. Store the Base64, letters, and S3 location in DynamoDB

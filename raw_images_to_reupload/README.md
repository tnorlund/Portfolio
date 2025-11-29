# Raw Images for Re-upload

These images need to be re-uploaded to trigger proper OCR processing for receipt regions.

## Images

1. **abaec508-6730-4d75-9d48-76492a26a168.png** (26.5 MB)
   - Type: PHOTO
   - Dimensions: 4284x5712
   - Issue: Receipt 1 is cropped from image, needs proper receipt OCR

2. **cb22100f-44c2-4b7d-b29f-46627a64355a.png** (2.2 MB)
   - Type: SCAN
   - Dimensions: 2548x3508
   - Issue: Receipt 3 is a small region, needs proper receipt OCR

## Before Re-uploading

⚠️ **Important**: Before re-uploading, you should delete the incorrectly populated receipt lines/words/letters that were added earlier. These were copied from image OCR and have incorrect coordinates for the receipt regions.

To delete them:
```python
# Use the populate script in reverse, or manually delete:
# - Receipt lines for these receipts
# - Receipt words for these receipts  
# - Receipt letters for these receipts
```

## Re-upload Process

1. Re-upload these images through your upload lambda
2. The upload lambda will:
   - Detect image type (PHOTO/SCAN)
   - Run OCR on the full image
   - Cluster lines to detect receipts
   - For each receipt: crop region → run OCR → process properly
   - Create receipt lines/words/letters with correct coordinates

## Note

If the images already exist (same SHA256), the upload might:
- Skip creating a duplicate image
- But still process the receipts properly
- Or you may need to delete the existing image records first

Check with your upload lambda behavior for duplicate handling.

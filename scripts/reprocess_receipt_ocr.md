# Re-process Receipt OCR

The two receipts that were missing OCR data need to be properly re-processed:

1. **Image `abaec508-6730-4d75-9d48-76492a26a168`, Receipt 1** (PHOTO type)
   - Receipt is cropped from full image
   - Needs: Download raw image → Crop receipt region → Run OCR → Process

2. **Image `cb22100f-44c2-4b7d-b29f-46627a64355a`, Receipt 3** (SCAN type)  
   - Receipt is a small region in a scanned document
   - Needs: Download raw image → Crop receipt region → Run OCR → Process

## Recommended Approach

Since these are already in the system, the best approach is:

1. **Delete the incorrectly populated receipt lines/words** (what we just added)
2. **Download the raw image from S3**
3. **Crop/warp the receipt region** based on receipt bounding box
4. **Run OCR on the cropped receipt** using the Swift OCR script
5. **Process the OCR results** through the receipt processing pipeline
6. **Upload the cropped receipt image** to S3 (if needed)

OR, simpler:
- **Delete the receipt and re-upload the raw image** through the upload lambda
- This will trigger the proper OCR processing pipeline

Which approach would you prefer?

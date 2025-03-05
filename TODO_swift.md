# TODO List for Improved Address Detection Implementation

## 1. Review the Existing Code
- **Examine `NSDataDetector` Usage:**  
  Check where `NSDataDetector` is currently used (e.g., inside loops that process single words or short lines).
- **Identify Text Segmentation:**  
  Locate the parts of the OCR pipeline where text is split into words or lines.

## 2. Plan the Text Aggregation
- **Determine Aggregation Strategy:**  
  Decide how to combine text from OCR results (e.g., joining all recognized lines into one continuous string with a space or newline separator).
- **Update Code:**  
  Modify the code to build an aggregated text string immediately after OCR processing.

## 3. Implement a New Data Extraction Function
- **Create Function:**  
  Write a new function, for example `extractAllStructuredData(from:)`, which accepts the aggregated text.
- **Initialize NSDataDetector:**  
  Configure `NSDataDetector` with the desired checking types (e.g., `.address`, `.phoneNumber`, `.date`, `.link`).
- **Process Full Text:**  
  Use the detector on the entire aggregated text to capture **all** matching entities rather than just the first one.
- **Return Results:**  
  Return the detected items as an array of structured data objects (e.g., addresses, phone numbers).

## 4. Integrate the New Function into the Main Workflow
- **Call the New Function:**  
  After OCR processing and text aggregation, invoke your new data extraction function.
- **Filter Results:**  
  If needed, filter the extracted data to focus on addresses or any other type.
- **Update Output:**  
  Adjust the output (e.g., JSON or log) to include the newly extracted structured data.

## 5. Test and Validate
- **Run Tests:**  
  Use sample images that contain multiple addresses to verify that the full-text context improves detection.
- **Validate Accuracy:**  
  Ensure that multi-word and multi-line addresses are correctly identified.
- **Refine Aggregation:**  
  Tweak the aggregation method (using spaces or newlines) based on test outcomes.

## 6. Refactor and Clean Up
- **Remove Obsolete Code:**  
  Comment out or remove the old per-word extraction code if it’s no longer required.
- **Add Documentation:**  
  Insert comments and documentation to explain the new approach.
- **Optional Configuration:**  
  Consider adding configuration options to toggle between per-word and full-text processing if needed.

## 7. Deploy and Monitor
- **Integrate Changes:**  
  Merge the updated code into your main project.
- **Monitor Performance:**  
  Keep track of detection performance and accuracy in production.
- **Iterate:**  
  Make further adjustments based on real-world performance and user feedback.
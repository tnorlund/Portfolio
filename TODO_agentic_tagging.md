# TODO: Enrich OCR Entity Labels via External API

## ✅ Preparation
- [ ] Review existing OCR dataset for labeled entities (dates, addresses, phone numbers, links).
- [ ] Extract a representative subset of receipts for initial testing.

## 🧑‍💻 Integration Implementation
- [ ] Write script to extract high-confidence entities from OCR dataset.
- [ ] Select external API (e.g., Google Places, Yelp Fusion).
- [ ] Implement API queries using:
  - [ ] Phone numbers → Business details
  - [ ] Addresses → Verify store information
  - [ ] URLs → Retrieve additional metadata

## 🤖 GPT-based Validation Pipeline
- [ ] Craft GPT prompts to validate API results against original OCR data.
  - Example: `"Given OCR address '123 Main St.' and API returned store 'Apple Store', validate if this match is correct."`
- [ ] Integrate GPT validation into pipeline.

## 🔄 Automation & Scaling
- [ ] Develop automated pipeline:
  - OCR extraction → API enrichment → GPT validation → Dataset expansion
- [ ] Test the automation loop on a subset of data.

## 📈 Evaluation & Optimization
- [ ] Manually review GPT validation results for accuracy.
- [ ] Refine heuristics or GPT prompts based on evaluation.

## 🚀 Full Deployment
- [ ] Run the finalized pipeline on the full OCR dataset.
- [ ] Continuously monitor and refine the pipeline.

## 📖 Documentation
- [ ] Clearly document all steps, APIs, GPT prompts, and code implementations.
- [ ] Maintain documentation for iterative improvement.
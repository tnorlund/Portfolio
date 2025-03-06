# TODO: Enrich OCR Entity Labels via External API

## ✅ Preparation

### Overall Coverage:
- Out of 265 total receipts, 264 (99.6%) had some form of extractable data
- Only 1 receipt (0.4%) had no extractable data at all
- This suggests the OCR system is very effective at finding structured data

### Most Common Combinations:
- The most comprehensive combination (address + date + phone + url) appears in 166 receipts (62.6%)
- The second most common is (address + date + phone) in 64 receipts (24.2%)
- Together, these two combinations cover 86.8% of all receipts, indicating most receipts have at least 3-4 types of data
### Distribution by Combinations:
- Full combination (4 types): 166 receipts (62.6%)
- Three types: 79 receipts (29.9%) total
  - address + date + phone: 64 (24.2%)
  - date + phone + url: 14 (5.3%)
  - address + date + url: 1 (0.4%)
- Two types: 12 receipts (4.5%) total
  - address + phone: 4 (1.5%)
  - date + phone: 4 (1.5%)
  - address + date: 3 (1.1%)
  - address + url: 1 (0.4%)
- Single type: 7 receipts (2.8%) total
  - address: 2 (0.8%)
  - phone: 2 (0.8%)
  - url: 2 (0.8%)
  - date: 1 (0.4%)
### Key Insights:
- The system is very good at extracting multiple types of data from each receipt
- Most receipts (92.5%) contain three or more types of extractable data
- Very few receipts (2.8%) have only one type of extractable data
- The combination of address, date, and phone number appears to be the most reliable set of extractable information

## 🧑‍💻 Integration Implementation

### ✅ Core Infrastructure
- [x] Created BatchPlacesProcessor class for systematic processing
- [x] Implemented confidence level tracking (HIGH, MEDIUM, LOW, NONE)
- [x] Added validation result structure with confidence scoring
- [x] Set up logging and error handling

### ✅ Medium Priority Implementation (4.5% of receipts)
- [x] Implemented strategy for receipts with 2 data points:
  - [x] Address + Phone validation (1.5% of receipts)
  - [x] Address + URL validation with website matching (0.4% of receipts)
  - [x] Phone + Date validation with manual review (1.5% of receipts)
  - [x] Address + Date validation with manual review (1.1% of receipts)

### 🚧 High Priority Implementation (92.5% of receipts)
- [ ] Implement core validation methods:
  - [ ] _validate_with_address_and_phone method
  - [ ] _try_fallback_strategies method
- [ ] Create batch processing for different combinations:
  - [ ] Complete data sets (address + date + phone + url) - 62.6%
  - [ ] Three data points (address + date + phone) - 24.2%
  - [ ] Other three point combinations - 5.7%

### 🚧 Edge Cases (3.2% of receipts)
- [x] Added warning system for unimplemented cases
- [ ] Implement low priority receipt handling (2.8% of receipts):
  - [ ] Single address (0.8%)
  - [ ] Single phone (0.8%)
  - [ ] Single URL (0.8%)
  - [ ] Single date (0.4%)
- [ ] Implement no-data receipt handling (0.4% of receipts)

### 🚧 API Integration Priorities
1. [ ] Google Places API integration:
   - [x] Basic API client integration
   - [x] Individual query handling
   - [ ] Batch query implementation
   - [x] Rate limiting and error handling

2. [ ] Fallback API integration (e.g., Yelp Fusion):
   - [ ] Secondary verification for uncertain matches
   - [ ] Additional metadata enrichment
   - [ ] Cross-reference validation

### ✅ Quality Assurance
- [x] Implemented confidence scoring system:
  - [x] High confidence: 3+ matching data points
  - [x] Medium confidence: 2 matching data points
  - [x] Low confidence: Single data point match
  - [x] Manual review triggers for uncertain cases

## 🤖 GPT-based Validation Pipeline
- [ ] Craft GPT prompts to validate API results against original OCR data.
  - Example: `"Given OCR address '123 Main St.' and API returned store 'Apple Store', validate if this match is correct."`
- [ ] See if there are additional tags to get:
  - [ ] Dates
  - [ ] Times
  - [ ] Prices
  - [ ] Totals
  - [ ] Tax amounts
  - [ ] Card numbers (masked)
  - [ ] Item descriptions
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
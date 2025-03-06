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

### Key Performance Insights:
- System achieves 83.0% successful match rate
- 81.5% of matches are high confidence
- 18.1% require manual review
- Primary validation relies on address and phone number matching
- Generic business types (street_address, route, department_store) dominate results

## 🧑‍💻 Implementation Roadmap

### ✅ Completed Tasks
- [x] Core Infrastructure:
  - BatchPlacesProcessor class
  - Confidence level tracking (HIGH, MEDIUM, LOW, NONE)
  - Validation result structure
  - Logging and error handling
- [x] Two-Point Receipt Strategy (4.5% coverage)
- [x] Quality Assurance System
- [x] GPT-based Structure Analysis:
  - Implemented two-stage prompting system
  - Added Places API integration
  - Created validation pipeline
  - Achieved 90% average confidence across sections

### 🚧 High Priority Tasks (92.5% coverage)
- [ ] Address Processing Enhancement:
  - [ ] Implement libpostal integration
  - [ ] Add fuzzy matching and geolocation support
- [x] Multi-API Integration:
  - [x] Primary: Optimize Google Places API usage
  - [ ] Secondary: Add Yelp/OpenStreetMap for validation
- [x] Validation Logic Optimization:
  - [x] Implement adaptive confidence scoring
  - [ ] Add geographic search expansion
  - [x] Create validation layers

### 🤖 GPT-based Validation Pipeline
- [x] Two-Stage Prompt System:
  1. Structure Recognition
     - ✓ Implemented spatial pattern detection
     - ✓ Added business context integration
     - ✓ Achieved 90% confidence threshold
  2. Field Labeling (Word-Level Tagging)
     - ✓ Section-aware labeling
     - ✓ Confidence scoring
     - ✓ Pattern validation
     - [x] Optimized semantic label set:
       * Section-specific label types (e.g., business_name, address_line for business section)
       * High-confidence tagging (95% average)
       * Clear label definitions with examples
       * Reduced ambiguity by eliminating generic labels
     - [ ] Future label enhancements:
       * Add specialized labels for unique business types
       * Support multi-language receipt formats
       * Handle special promotional content
       * Track label distribution statistics

- [x] Data Source Integration:
  - [x] Places API (Ground Truth)
  - [x] GPT (Receipt-Specific Data)
- [x] Validation Rules Implementation:
  - [x] Internal consistency checks
  - [x] Cross-reference validations
  - [x] Pattern validation

### 📊 LayoutLM Dataset Preparation
- [ ] Data Format Conversion:
  - [ ] Convert word tags to IOB format (B-tag, I-tag, O)
  - [ ] Normalize bounding boxes to 1000x1000 space
  - [ ] Generate image features
  - [ ] Create dataset splits (train/val/test)

- [ ] Label Quality Assurance:
  - [ ] Implement label consistency checks
  - [ ] Add validation UI for human review
  - [ ] Track inter-annotator agreement
  - [ ] Create label validation guidelines

- [ ] Dataset Statistics:
  - [ ] Track label distribution
  - [ ] Monitor spatial coverage
  - [ ] Analyze label co-occurrence
  - [ ] Report dataset balance metrics

- [ ] Dataset Documentation:
  - [ ] Label taxonomy and definitions
  - [ ] Annotation guidelines
  - [ ] Dataset statistics and visualizations
  - [ ] Data format specifications

### 🔄 System Improvements
- [ ] Machine Learning:
  - [ ] Train review prediction model
  - [ ] Implement pattern recognition
- [ ] Edge Cases (3.2%):
  - [ ] Single data point handling
  - [ ] No-data receipt processing
- [ ] Automation:
  - [ ] Pipeline orchestration
  - [ ] Error handling and retries
  - [ ] Performance monitoring

### 📖 Documentation & Maintenance
- [ ] Technical Documentation:
  - [ ] API integration specs
  - [ ] Validation strategies
  - [ ] Error handling procedures
- [ ] Operational Guides:
  - [ ] API key management
  - [ ] Model maintenance
  - [ ] Monitoring procedures

## 📊 Reference Templates

### 🎯 Prompt Development Strategy
- [x] Implement Two-Stage Prompting System:
  1. Structure Recognition Prompt:
     - ✓ Input: Raw receipt text + Places API context
     - ✓ Output: Section boundaries and confidence
     - ✓ Performance: 90% average confidence

  2. Field Labeling Prompt (Word-Level Tagging):
     - ✓ Input: Receipt text + Section boundaries + Places API context
     - ✓ Output: Word-level semantic labels with confidence
     - ✓ Integration: Spatial and business context awareness
     - [ ] Enhancement: Extended label set for LayoutLM training

- [ ] Data Source Separation:
  1. Places API Provides (Use as Ground Truth):
     - Business Identity:
       * Name and brand
       * Business categories (types)
       * Price level and rating
       * Business status
     - Location Data:
       * Formatted address
       * Coordinates (geometry)
       * Vicinity/neighborhood
     - Contact Info:
       * Phone numbers (formatted and international)
       * Website URL
     - Operating Info:
       * Opening hours
       * Plus code

  2. GPT Labels (Receipt-Specific Data):
     - Receipt Structure:
       * Section boundaries
       * Layout patterns
       * Field positions
     - Transaction Details:
       * Item descriptions
       * Individual prices
       * Quantity markers
     - Financial Data:
       * Subtotals
       * Tax calculations
       * Final totals
     - Payment Info:
       * Payment method
       * Card details (masked)
       * Transaction ID/reference
     - Time Data:
       * Transaction date
       * Transaction time
       * Order/pickup numbers

- [ ] Validation Strategy:
  1. Use Places API Data For:
     - Business identity confirmation
     - Address verification
     - Phone number validation
     - Operating hours verification
     - Business type context

  2. Use GPT For:
     - Receipt format understanding
     - Line item extraction
     - Price/total validation
     - Payment detail parsing
     - Transaction metadata extraction

### 📊 Data Point Extraction Enhancement
- [ ] Business Context Extraction:
  - [x] Business Name and Identity:
    - Achieved 95% confidence in business_name tagging
    - Accurate store_id identification
    - Reliable address component mapping
  - [ ] Operating Context:
    - Map business hours to structured format
    - Extract service type indicators
    - Identify branch/location specifiers
  - [ ] Contact Information:
    - Phone number standardization
    - Website/social media extraction
    - Additional contact methods

- [ ] Transaction Data Extraction:
  - [x] Core Transaction Elements:
    - High-confidence date/time tagging
    - Accurate total amount identification
    - Clear payment method classification
  - [ ] Enhanced Transaction Details:
    - Member/loyalty program information
    - Transaction type classification
    - Special order indicators
  - [ ] Item-Level Details:
    - Product category mapping
    - Unit price extraction
    - Discount calculation validation

- [ ] Payment Data Extraction:
  - [ ] Card Numbers:
    - Identify masked patterns
    - Map to payment section
    - Track position relative to total
  - [ ] Item Descriptions:
    - Map to line items
    - Link to prices
    - Use business type for context

### 🔍 Validation Rules
- [ ] Implement Hierarchical Validation:
  1. Internal Consistency:
     - Check price totals match
     - Verify tax calculations
     - Validate date/time against hours
  2. External Validation:
     - Cross-reference with Places API
     - Verify address format
     - Validate business type consistency
  3. Pattern Validation:
     - Check expected receipt structure
     - Verify section ordering
     - Validate field positions

### 🎨 Prompt Engineering

1. Structure Recognition Prompt Template:
   ```json
   {
     "system_context": "You are analyzing receipt structure. Use the provided business context to identify receipt sections.",
     
     "input_data": {
       "receipt_text": {
         "lines": [
           {
             "line_id": "integer",
             "text": "string",
             "words": [
               {
                 "word_id": "integer",
                 "text": "string",
                 "bounding_box": {
                   "x": "float",
                   "y": "float",
                   "width": "float",
                   "height": "float"
                 }
               }
             ]
           }
         ]
       },
       "business_context": {
         "name": "string (from Places API)",
         "type": ["string array (from Places API types)"],
         "address": "string (from Places API)",
         "operating_hours": "string (from Places API)"
       }
     },

     "task": {
       "objective": "Identify the following sections in the receipt",
       "required_sections": [
         {
           "name": "header",
           "expected_content": ["business name", "address", "phone", "date/time"]
         },
         {
           "name": "items",
           "expected_content": ["item descriptions", "quantities", "prices"]
         },
         {
           "name": "totals",
           "expected_content": ["subtotal", "tax", "total"]
         },
         {
           "name": "footer",
           "expected_content": ["payment method", "transaction id", "thank you message"]
         }
       ]
     },

     "output_format": {
       "sections": {
         "header": {
           "start_line": "integer",
           "end_line": "integer",
           "confidence": "float",
           "detected_fields": ["string array of found fields"]
         },
         "items": {
           "start_line": "integer",
           "end_line": "integer",
           "confidence": "float",
           "item_count": "integer"
         },
         "totals": {
           "start_line": "integer",
           "end_line": "integer",
           "confidence": "float",
           "detected_fields": ["string array of found fields"]
         },
         "footer": {
           "start_line": "integer",
           "end_line": "integer",
           "confidence": "float",
           "detected_fields": ["string array of found fields"]
         }
       },
       "overall_confidence": "float"
     }
   }
   ```

2. Field Labeling Prompt Template:
   ```json
   {
     "system_context": "You are labeling individual words in a receipt. Use the section boundaries and business context for accurate labeling.",
     
     "input_data": {
       "receipt_text": {
         "lines": [
           {
             "line_id": "integer",
             "text": "string",
             "section": "string (header/items/totals/footer)",
             "words": [
               {
                 "word_id": "integer",
                 "text": "string",
                 "bounding_box": {
                   "x": "float",
                   "y": "float",
                   "width": "float",
                   "height": "float"
                 }
               }
             ]
           }
         ]
       },
       "section_boundaries": {
         // Output from Structure Recognition Prompt
       },
       "business_context": {
         // Same as Structure Recognition Prompt
       }
     },

     "task": {
       "objective": "Label individual words with their semantic roles",
       "label_types": {
         "item_description": {
           "description": "Product or service name",
           "example": "Large Coffee"
         },
         "quantity": {
           "description": "Number of items",
           "example": "2"
         },
         "price": {
           "description": "Individual or total price",
           "example": "3.99"
         },
         "subtotal": {
           "description": "Pre-tax total",
           "example": "Subtotal: 15.98"
         },
         "tax": {
           "description": "Tax amount or rate",
           "example": "Tax: 1.28"
         },
         "total": {
           "description": "Final total amount",
           "example": "Total: 17.26"
         },
         "payment_method": {
           "description": "Form of payment",
           "example": "VISA"
         },
         "transaction_id": {
           "description": "Unique transaction identifier",
           "example": "Ref#: 123456"
         },
         "date_time": {
           "description": "Transaction date and time",
           "example": "2024-01-20 14:30"
         }
       }
     },

     "output_format": {
       "labels": [
         {
           "line_id": "integer",
           "word_id": "integer",
           "label": "string (from label_types)",
           "confidence": "float",
           "group_id": "string (for multi-word entities)",
           "validation": {
             "pattern_match": "boolean",
             "context_appropriate": "boolean",
             "section_appropriate": "boolean"
           }
         }
       ],
       "metadata": {
         "total_labeled_words": "integer",
         "average_confidence": "float",
         "requires_review": "boolean",
         "review_reasons": ["string array"]
       }
     }
   }
   ```

3. Prompt Chain Integration:
   ```json
   {
     "pipeline": {
       "1_structure_recognition": {
         "input": "receipt_text + business_context",
         "output": "section_boundaries",
         "validation": {
           "required_sections": "boolean",
           "logical_ordering": "boolean",
           "confidence_threshold": 0.8
         }
       },
       "2_field_labeling": {
         "input": "receipt_text + section_boundaries + business_context",
         "output": "word_labels",
         "validation": {
           "required_fields": "boolean",
           "label_consistency": "boolean",
           "confidence_threshold": 0.9
         }
       }
     },
     "error_handling": {
       "low_confidence": "flag for review",
       "missing_sections": "retry with adjusted thresholds",
       "inconsistent_labels": "apply business rules"
     }
   }
   ```

### 🔄 Continuous Improvement
- [ ] Implement Feedback Loop:
  - Store successful mappings
  - Track validation patterns
  - Update prompt based on accuracy
  - Build business-type-specific rules

## 🔄 Automation & Scaling
- [ ] Develop Automated Pipeline:
  - [ ] OCR extraction → API enrichment → GPT validation → Dataset expansion
  - [ ] Implement retry strategies for failed validations
  - [ ] Add cross-validation with multiple APIs
- [ ] Test automation loop on data subsets
- [ ] Monitor and optimize API usage

## 📈 Evaluation & Optimization
- [ ] Conduct Detailed Error Analysis:
  - [ ] Review all no-match cases
  - [ ] Analyze low-confidence patterns
  - [ ] Identify systematic validation issues
- [ ] Optimize Confidence Thresholds:
  - [ ] Analyze historical validation outcomes
  - [ ] Adjust thresholds by business type
  - [ ] Fine-tune manual review triggers

## 📖 Documentation
- [ ] Document Implementation Details:
  - [ ] API integration specifications
  - [ ] Validation strategies
  - [ ] Machine learning models
  - [ ] Error handling procedures
- [ ] Create Maintenance Guides:
  - [ ] API key rotation procedures
  - [ ] Model retraining guidelines
  - [ ] Performance monitoring protocols

### 🔍 Structure Analysis Results (20 Receipt Sample)
- Overall Performance:
  - 100% successful processing rate
  - 0.90 average structure confidence
  - 0.95 average word-level confidence
  - Zero unknown labels in critical fields

- Section Distribution:
  - business_info: 55% coverage, 0.94 confidence
    * 100% accuracy in business_name
    * 98% accuracy in address_line
    * 95% accuracy in phone/store_id
  - payment_details: 45% coverage, 0.88 confidence
    * 100% accuracy in total amounts
    * 95% accuracy in payment methods
    * 90% accuracy in card information
  - transaction_details: 40% coverage, 0.90 confidence
    * 98% accuracy in date/time
    * 95% accuracy in transaction IDs
  - purchase_details: 35% coverage, 0.88 confidence
    * 92% accuracy in item identification
    * 90% accuracy in price mapping

- Pattern Recognition Improvements:
  - Spatial Patterns:
    * Consistent business header alignment
    * Reliable total amount positioning
    * Clear section separation detection
  - Content Patterns:
    * Strong business context matching
    * Accurate payment flow identification
    * Reliable item list structure

- Areas for Optimization:
  - Special Cases:
    * Holiday/promotional content
    * Multi-language receipts
    * Custom store formats
  - Edge Cases:
    * Handwritten modifications
    * Damaged/partial receipts
    * Non-standard layouts

### 📊 Updated Performance Metrics
- Structure Recognition:
  - 100% processing success rate
  - 90% average confidence
  - 55% business info detection
  - 45% payment details detection
  - 40% transaction details detection

- Pattern Detection:
  - Spatial patterns: 4 primary types identified
  - Content patterns: Consistent across similar businesses
  - Section confidence: 0.75-0.98 range
  - Error rate: < 1% processing errors

- Areas for Improvement:
  - Membership sections (0.29 confidence)
  - Return policy detection
  - Special promotion handling
  - Edge case management
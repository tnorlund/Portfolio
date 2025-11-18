# CoVe Verification Storage and Batch Validation

## Overview

This system stores Chain of Verification (CoVe) verification records in DynamoDB and uses them to validate PENDING/NEEDS_REVIEW labels efficiently by reusing verification questions and answers.

## Architecture

### Entities

#### 1. ReceiptWordLabelCoVeVerification

Stores the full CoVe verification chain for a ReceiptWordLabel:

- **Verification Questions**: Questions generated to verify the label
- **Verification Answers**: Answers to those questions with evidence
- **Revision Info**: Whether revision was needed and why
- **Template Metadata**: Whether this verification can be reused as a template

**Key Fields:**
- `verification_questions`: List of verification questions (from VerificationQuestionsResponse)
- `verification_answers`: List of verification answers (from VerificationAnswersResponse)
- `initial_label`: Label before CoVe
- `final_label`: Label after CoVe
- `cove_verified`: Whether CoVe completed successfully
- `can_be_reused_as_template`: Whether this verification can be reused
- `template_applicability_score`: Score (0-1) indicating how applicable this template is

**GSI1**: Query by label type + verification status
**GSI2**: Query by merchant + label type (for merchant-specific templates)

#### 2. ReceiptCoreLabelDefinition

Stores CORE_LABEL definitions in DynamoDB for reference:

- **label_type**: The label type (e.g., "GRAND_TOTAL")
- **description**: Description of what this label represents
- **category**: Category grouping (e.g., "totals_taxes", "transaction_info")

**GSI1**: Query by category

## Usage

### 1. Storing CoVe Verification Records

When CoVe verification runs, store the verification records:

```python
from receipt_label.langchain.utils.cove_storage import create_cove_verification_record
from receipt_dynamo.client import DynamoClient

# After CoVe verification
verification_record = create_cove_verification_record(
    label=receipt_word_label,
    questions_response=questions_response,
    answers_response=answers_response,
    initial_label="GRAND_TOTAL",
    final_label="GRAND_TOTAL",
    word_text="24.01",
    merchant_name="Costco",
    llm_model="gpt-oss:120b",
)

dynamo_client.add_receipt_word_label_cove_verification(verification_record)
```

### 2. Batch Validation Using Templates

Validate PENDING labels by reusing CoVe verification questions:

```python
from receipt_label.langchain.utils.batch_validation_cove import (
    validate_labels_using_cove_templates,
)

# Validate labels using templates
validated_labels = await validate_labels_using_cove_templates(
    pending_labels=pending_labels,
    receipt_text=receipt_text,
    llm=llm,
    dynamo_client=dynamo_client,
    word_text_lookup=word_text_lookup,
    merchant_name="Costco",  # Optional: use merchant-specific templates
)

# Update DynamoDB
dynamo_client.update_receipt_word_labels(validated_labels)
```

### 3. Running Batch Validation Script

Use the batch validation script to process all PENDING/NEEDS_REVIEW labels:

```bash
# Validate all PENDING/NEEDS_REVIEW labels
python dev.batch_validate_labels_cove.py

# Validate specific label type
python dev.batch_validate_labels_cove.py --label-type GRAND_TOTAL

# Validate with limit
python dev.batch_validate_labels_cove.py --limit 1000

# Dry run (don't update DynamoDB)
python dev.batch_validate_labels_cove.py --dry-run
```

## How It Works

### Template-Based Validation

1. **Query Templates**: Find successful CoVe verifications for the label type
2. **Select Best Template**: Choose template with highest applicability score
3. **Reuse Questions**: Use verification questions from the template
4. **Answer Questions**: Answer questions for the new label using receipt text
5. **Determine Validity**: Based on answer confidence and revision requirements

### Merchant-Level Templates

- Templates are prioritized by merchant name
- Merchant-specific patterns are leveraged (e.g., Costco always puts GRAND_TOTAL at bottom)
- Falls back to general templates if no merchant-specific ones found

### Pattern Matching

- Find similar successful verifications by word text
- Calculate success rate across similar verifications
- Validate based on success rate threshold

## Benefits

1. **Cost Efficiency**: Reuse verification questions instead of generating new ones
2. **Speed**: Template matching is faster than full CoVe
3. **Consistency**: Same verification logic across similar labels
4. **Scalability**: Validate thousands of labels using hundreds of templates
5. **Auditability**: Full verification chain stored for debugging

## Data Model

### ReceiptWordLabelCoVeVerification

```
PK: IMAGE#<image_id>
SK: RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>#COVE
GSI1PK: LABEL#<label>#COVE
GSI1SK: VERIFIED#<true/false>#<timestamp>
GSI2PK: MERCHANT#<merchant_name>#LABEL#<label>
GSI2SK: TEMPLATE#<true/false>#<timestamp>
```

### ReceiptCoreLabelDefinition

```
PK: CORE_LABEL_DEFINITION
SK: LABEL#<label_type>
GSI1PK: CATEGORY#<category>
GSI1SK: LABEL#<label_type>
```

## Example: Validating Your Labels

Given your label counts JSON:
- 51 PENDING GRAND_TOTAL labels
- 760 VALID GRAND_TOTAL labels (with CoVe records)

**Strategy:**
1. Use CoVe records from the 760 VALID GRAND_TOTAL labels as templates
2. Apply those verification questions to the 51 PENDING labels
3. Answer questions and determine validity

This could validate most of the 51 PENDING labels without running full CoVe!

## Next Steps

1. **Populate CORE_LABELS**: Run a migration script to populate ReceiptCoreLabelDefinition
2. **Store Existing CoVe Records**: Backfill CoVe verification records for existing VALID labels
3. **Run Batch Validation**: Execute `dev.batch_validate_labels_cove.py` to validate PENDING labels
4. **Monitor Results**: Track validation rates and adjust template selection logic

## Integration with Existing Validation

This system complements existing validation methods:

1. **Rule-based validation**: Fast, cheap validation for structured formats
2. **ChromaDB similarity**: Pattern-based validation using embeddings
3. **CoVe templates**: Reuse verification questions from successful CoVe runs
4. **Full CoVe**: Run full CoVe for labels that can't be validated by templates

The system uses a tiered approach, trying cheaper methods first before falling back to more expensive ones.


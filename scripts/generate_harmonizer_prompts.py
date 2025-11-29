#!/usr/bin/env python3
"""
Generate improved prompts for the label harmonizer by analyzing patterns in
VALID, INVALID, and NEEDS_REVIEW labels.

This script:
1. Collects examples of VALID, INVALID, and NEEDS_REVIEW labels for a CORE_LABEL
2. Analyzes patterns that lead to each status
3. Uses LLM to generate improved prompts for the harmonizer
"""

import asyncio
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ValidationStatus

# Import CORE_LABELS
try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available
    CORE_LABELS = {
        "MERCHANT_NAME": "The name of the business/store where the receipt is from",
        "PRODUCT_NAME": "The name of a product or item purchased",
        "ADDRESS_LINE": "Part of the business address",
        # Add more as needed
    }

try:
    from receipt_agent.config.settings import get_settings
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage
    from langchain_core.runnables import RunnableConfig
    from pydantic import BaseModel, Field
except ImportError:
    print("Error: receipt_agent package not found. Install dependencies first.")
    sys.exit(1)


class PromptImprovementGenerator:
    """Generate improved prompts for the harmonizer based on label patterns."""

    def __init__(self, dynamo: DynamoClient, llm: Optional[Any] = None):
        self.dynamo = dynamo
        if llm is None:
            # Initialize LLM from Pulumi secrets
            try:
                from receipt_dynamo.data._pulumi import load_secrets

                secrets = load_secrets("dev") or load_secrets("prod")
                api_key = secrets.get("OLLAMA_API_KEY") if secrets else None
                base_url = secrets.get("OLLAMA_BASE_URL", "https://ollama.com")
                model = secrets.get("OLLAMA_MODEL", "gpt-oss:120b-cloud")

                # Fall back to settings if not in Pulumi
                if not api_key:
                    settings = get_settings()
                    api_key_str = settings.ollama_api_key
                    if hasattr(api_key_str, 'get_secret_value'):
                        api_key = api_key_str.get_secret_value()
                    else:
                        api_key = str(api_key_str) if api_key_str else None
                    base_url = settings.ollama_base_url
                    model = settings.ollama_model

                print(f"Using Ollama model: {model} at {base_url}")

                self.llm = ChatOllama(
                    base_url=base_url,
                    model=model,
                    client_kwargs={
                        "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
                        "timeout": 120,
                    },
                    temperature=0.0,
                )
            except Exception as e:
                print(f"Error initializing LLM: {e}")
                self.llm = None
        else:
            self.llm = llm

    async def generate_prompt_improvements(
        self,
        label_type: str,
        max_examples_per_status: int = 20,
    ) -> Dict[str, Any]:
        """
        Generate improved prompts for the harmonizer.

        Args:
            label_type: CORE_LABEL to analyze
            max_examples_per_status: Maximum examples to collect per validation status

        Returns:
            Dict with improved prompts and analysis
        """
        print(f"Phase 1: Collecting labels for {label_type}...")
        labels_by_status = self._collect_labels_by_status(label_type, max_examples_per_status)

        print(f"  VALID: {len(labels_by_status.get('VALID', []))}")
        print(f"  INVALID: {len(labels_by_status.get('INVALID', []))}")
        print(f"  NEEDS_REVIEW: {len(labels_by_status.get('NEEDS_REVIEW', []))}")

        if not any(labels_by_status.values()):
            print("  No labels found!")
            return {}

        print(f"\nPhase 2: Enriching labels with context...")
        enriched = await self._enrich_labels_with_context(labels_by_status)

        print(f"\nPhase 3: Analyzing patterns with LLM...")
        improvements = await self._analyze_patterns_and_generate_prompts(
            label_type, enriched
        )

        return improvements

    def _collect_labels_by_status(
        self, label_type: str, max_per_status: int
    ) -> Dict[str, List]:
        """Collect labels grouped by validation status."""
        labels_by_status = defaultdict(list)

        for status in [ValidationStatus.VALID, ValidationStatus.INVALID, ValidationStatus.NEEDS_REVIEW]:
            last_key = None
            collected = 0

            while collected < max_per_status:
                batch, last_key = self.dynamo.list_receipt_word_labels_with_status(
                    status=status,
                    limit=min(1000, max_per_status - collected),
                    last_evaluated_key=last_key,
                )

                # Filter by label_type
                batch = [l for l in batch if l.label == label_type.upper()]

                remaining = max_per_status - collected
                labels_by_status[status.value].extend(batch[:remaining])
                collected += len(batch[:remaining])

                if not last_key or collected >= max_per_status:
                    break

        return dict(labels_by_status)

    async def _enrich_labels_with_context(
        self, labels_by_status: Dict[str, List]
    ) -> Dict[str, List[Dict]]:
        """Enrich labels with word text, merchant name, line context, etc."""
        enriched = defaultdict(list)

        for status, labels in labels_by_status.items():
            for label in labels:
                try:
                    # Get word text
                    word = self.dynamo.get_receipt_word(
                        receipt_id=label.receipt_id,
                        image_id=label.image_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                    )
                    word_text = word.text if word else ""

                    # Get receipt metadata
                    metadata = self.dynamo.get_receipt_metadata(
                        label.image_id, label.receipt_id
                    )
                    merchant_name = metadata.merchant_name if metadata else None

                    # Get line context
                    line = self.dynamo.get_receipt_line(
                        receipt_id=label.receipt_id,
                        image_id=label.image_id,
                        line_id=label.line_id,
                    )
                    line_text = line.text if line else ""

                    # Get surrounding lines
                    all_lines = self.dynamo.list_receipt_lines_from_receipt(
                        image_id=label.image_id, receipt_id=label.receipt_id
                    )
                    surrounding_lines = []
                    if all_lines and line:
                        all_lines.sort(key=lambda l: l.line_id)
                        try:
                            line_index = next(
                                i
                                for i, l in enumerate(all_lines)
                                if l.line_id == label.line_id
                            )
                            start_idx = max(0, line_index - 2)
                            end_idx = min(len(all_lines), line_index + 3)
                            surrounding_lines = [
                                l.text for l in all_lines[start_idx:end_idx]
                            ]
                            # Mark target line
                            target_idx = line_index - start_idx
                            if surrounding_lines:
                                surrounding_lines[target_idx] = f">>> {surrounding_lines[target_idx]} <<<"
                        except StopIteration:
                            surrounding_lines = [l.text for l in all_lines[:5]]

                    enriched[status].append({
                        "word_text": word_text,
                        "merchant_name": merchant_name,
                        "line_text": line_text,
                        "surrounding_lines": surrounding_lines,
                        "reasoning": label.reasoning,
                        "validation_status": label.validation_status,
                        "label_proposed_by": label.label_proposed_by,
                    })
                except Exception as e:
                    print(f"  Error enriching label: {e}")
                    continue

        return dict(enriched)

    async def _analyze_patterns_and_generate_prompts(
        self, label_type: str, enriched: Dict[str, List[Dict]]
    ) -> Dict[str, Any]:
        """Use LLM to analyze patterns and generate improved prompts."""
        if not self.llm:
            print("  LLM not available!")
            return {}

        # Build prompt with examples
        prompt = f"""# Analyze Label Patterns and Generate Improved Harmonizer Prompts

You are analyzing patterns in VALID, INVALID, and NEEDS_REVIEW labels for `{label_type}` to generate improved prompts for the label harmonizer.

## Goal

The harmonizer uses LLM prompts to:
1. **Validate suggestions**: Decide if a suggested label type is correct
2. **Suggest labels**: Propose a label type for outlier words

Your task is to identify patterns in what makes labels VALID vs INVALID vs NEEDS_REVIEW, and generate improved prompts that will help the harmonizer make better decisions.

## Label Type Definition

{CORE_LABELS.get(label_type, "N/A")}

## All CORE_LABELS (for context)

{chr(10).join(f"- **{k}**: {v}" for k, v in CORE_LABELS.items())}

## Examples by Status

"""

        # Add examples for each status
        for status in ["VALID", "INVALID", "NEEDS_REVIEW"]:
            examples = enriched.get(status, [])
            if not examples:
                continue

            prompt += f"\n### {status} Labels ({len(examples)} examples)\n\n"

            for i, ex in enumerate(examples[:10], 1):  # Show up to 10 per status
                prompt += f"**Example {i}:**\n"
                prompt += f"- Word: `{ex['word_text']}`\n"
                if ex.get('merchant_name'):
                    prompt += f"- Merchant: `{ex['merchant_name']}` (from Google Places)\n"
                prompt += f"- Line: `{ex['line_text']}`\n"
                if ex.get('surrounding_lines'):
                    prompt += f"- Context:\n"
                    for line in ex['surrounding_lines']:
                        prompt += f"  {line}\n"
                if ex.get('reasoning'):
                    prompt += f"- Reasoning: {ex['reasoning'][:200]}\n"
                prompt += "\n"

        prompt += f"""
## Current Harmonizer Prompts

The harmonizer currently uses these prompts:

### 1. Validation Prompt (for validating suggestions)
```
# Validate Label Type Suggestion

**Word:** `"{{word_text}}"` | **Merchant:** {{merchant_name}} | **Suggested Label:** `{label_type}`

**Your reasoning:** {{llm_reason}}

## Similar Words from Database (±3 lines context)
{{similar_words_text}}

## Instructions

Review the similar words above. Consider:
- **Similarity scores**: Higher similarity = more relevant
- **Merchant match**: Same merchant examples may be more relevant
- **Valid labels**: Words with `{label_type}` in their valid_labels support the suggestion
- **Invalid labels**: ⚠️ Words with `{label_type}` in their invalid_labels are a STRONG negative signal!

**Critical**: If you see similar words where `{label_type}` is in the invalid_labels, this is strong evidence the suggestion is wrong!

Is the suggestion valid?
```

### 2. Suggestion Prompt (for suggesting labels for outliers)
```
# Suggest Label Type for Outlier Word

**Word:** `"{{word_text}}"` | **Merchant:** {{merchant_name}} | **Current Label:** `{label_type}` (status: {{validation_status}})

**Context:**
{{context_lines}}

## Instructions

Based on the word, merchant, and context, suggest the correct CORE_LABEL type.

What label type should this word have?
```

## Your Task

Based on the examples above, identify:

1. **Patterns that lead to VALID labels**: What characteristics make a `{label_type}` label valid?
2. **Patterns that lead to INVALID labels**: What mistakes cause labels to be marked invalid?
3. **Patterns that lead to NEEDS_REVIEW**: What ambiguous cases need human review?
4. **Common confusion patterns**: What other label types are `{label_type}` often confused with?

Then, generate **improved prompts** for:
- **Validation prompt**: Better instructions for validating suggestions
- **Suggestion prompt**: Better instructions for suggesting labels

## Output Format

Provide a JSON response with:
- `patterns_analysis`: Analysis of patterns you identified
- `improved_validation_prompt`: Improved validation prompt (with placeholders like {{word_text}})
- `improved_suggestion_prompt`: Improved suggestion prompt (with placeholders)
- `key_improvements`: List of key improvements made to the prompts
- `common_mistakes_to_avoid`: List of common mistakes the harmonizer should avoid
"""

        try:
            class PromptImprovement(BaseModel):
                patterns_analysis: str = Field(description="Analysis of patterns in VALID/INVALID/NEEDS_REVIEW labels")
                improved_validation_prompt: str = Field(description="Improved validation prompt with placeholders")
                improved_suggestion_prompt: str = Field(description="Improved suggestion prompt with placeholders")
                key_improvements: List[str] = Field(description="List of key improvements made")
                common_mistakes_to_avoid: List[str] = Field(description="Common mistakes to avoid")

            messages = [HumanMessage(content=prompt)]
            config = RunnableConfig(
                run_name="generate_prompt_improvements",
                metadata={
                    "task": "prompt_improvement",
                    "label_type": label_type,
                },
                tags=["prompt-generator", "harmonizer-improvement"],
            )

            llm_with_schema = ChatOllama(
                model=self.llm.model,
                base_url=self.llm.base_url,
                client_kwargs=self.llm.client_kwargs,
                format=PromptImprovement.model_json_schema(),
                temperature=0.0,
            )
            llm_structured = llm_with_schema.with_structured_output(PromptImprovement)

            print("  Calling LLM for analysis...")

            # Save prompt for review
            prompt_file = f"prompt_improvement_{label_type.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(prompt)
            print(f"  Prompt saved to {prompt_file} for review")

            response = await llm_structured.ainvoke(messages, config=config)

            return {
                "label_type": label_type,
                "patterns_analysis": response.patterns_analysis,
                "improved_validation_prompt": response.improved_validation_prompt,
                "improved_suggestion_prompt": response.improved_suggestion_prompt,
                "key_improvements": response.key_improvements,
                "common_mistakes_to_avoid": response.common_mistakes_to_avoid,
            }

        except Exception as e:
            print(f"  Error in LLM analysis: {e}")
            import traceback
            traceback.print_exc()
            return {}


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate improved prompts for the label harmonizer"
    )
    parser.add_argument(
        "--label-type",
        type=str,
        required=True,
        help="CORE_LABEL to analyze (e.g., MERCHANT_NAME)",
    )
    parser.add_argument(
        "--max-examples-per-status",
        type=int,
        default=20,
        help="Maximum examples to collect per validation status (default: 20)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="harmonizer_prompt_improvements.json",
        help="Output JSON file path",
    )

    args = parser.parse_args()

    # Initialize clients
    try:
        from receipt_dynamo.data._pulumi import load_env

        pulumi_outputs = load_env("dev") or load_env("prod")

        if pulumi_outputs and "dynamodb_table_name" in pulumi_outputs:
            table_name = pulumi_outputs["dynamodb_table_name"]
            if "dynamodb_table_arn" in pulumi_outputs:
                arn = pulumi_outputs["dynamodb_table_arn"]
                region = arn.split(":")[3] if ":" in arn else pulumi_outputs.get("region", "us-east-1")
            else:
                region = pulumi_outputs.get("region", "us-east-1")
        else:
            settings = get_settings()
            table_name = settings.dynamo_table_name
            region = settings.aws_region

        dynamo = DynamoClient(table_name=table_name, region=region)
    except Exception as e:
        print(f"Error initializing DynamoDB client: {e}")
        sys.exit(1)

    # Generate prompt improvements
    generator = PromptImprovementGenerator(dynamo)

    print("=" * 60)
    print("Harmonizer Prompt Improvement Generator")
    print("=" * 60)
    print(f"Label Type: {args.label_type}")
    print(f"Max Examples per Status: {args.max_examples_per_status}")
    print(f"Output: {args.output}")
    print("=" * 60)
    print()

    try:
        improvements = await generator.generate_prompt_improvements(
            label_type=args.label_type,
            max_examples_per_status=args.max_examples_per_status,
        )

        # Save results
        output_data = {
            "generated_at": datetime.now().isoformat(),
            "label_type": args.label_type,
            "improvements": improvements,
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"\nResults saved to {args.output}")
        if improvements:
            print(f"\nKey Improvements:")
            for improvement in improvements.get("key_improvements", []):
                print(f"  - {improvement}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())


"""Zip Lambda handler: returns the 32 marquee questions.

This is the first step of the QA Agent Step Function pipeline.
Returns a list of questions with indices for the Map state to iterate over.
"""

import os

# All questions from QuestionMarquee.tsx
QUESTIONS = [
    "How much did I spend on groceries last month?",
    "What was my total spending at Costco?",
    "Show me all receipts with dairy products",
    "How much did I spend on coffee this year?",
    "What's my average grocery bill?",
    "Find all receipts from restaurants",
    "How much tax did I pay last quarter?",
    "What was my largest purchase this month?",
    "Show receipts with items over $50",
    "How much did I spend on organic products?",
    "What's the total for all gas station visits?",
    "Find all receipts with produce items",
    "How much did I spend on snacks?",
    "Show me pharmacy receipts from last week",
    "What was my food spending trend over 6 months?",
    "Find duplicate purchases across stores",
    "How much did I spend on beverages?",
    "Show all receipts with discounts applied",
    "What's the breakdown by store category?",
    "Find receipts where I bought milk",
    "How much did I spend on household items?",
    "Show receipts from the past 7 days",
    "What's my monthly spending average?",
    "Find all breakfast item purchases",
    "How much did I spend on pet food?",
    "Show receipts with loyalty points earned",
    "What was my cheapest grocery trip?",
    "Find all receipts with returns or refunds",
    "How much did I spend on frozen foods?",
    "Show me spending patterns by day of week",
    "What's my average tip at restaurants?",
    "Find receipts with handwritten notes",
]


def handler(event, context):
    """Return the list of marquee questions for the Map state.

    Returns:
        dict with execution_id, questions list, total count, and batch bucket.
    """
    execution_id = event.get("execution_id") or context.aws_request_id

    return {
        "execution_id": execution_id,
        "questions": [
            {"index": i, "text": q} for i, q in enumerate(QUESTIONS)
        ],
        "total_questions": len(QUESTIONS),
        "batch_bucket": os.environ["BATCH_BUCKET"],
    }

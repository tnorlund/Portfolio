"""Example of using the DynamoDB audit trail for learning and optimization.

This shows how to:
1. Query audit records
2. Analyze costs
3. Find optimization opportunities
4. Learn from failures
"""

import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

# Mock DynamoDB client for example
class MockDynamoClient:
    async def query(self, **kwargs):
        # Return mock data based on query
        pk = kwargs.get("ExpressionAttributeValues", {}).get(":pk", "")
        
        if "AUDIT#" in pk:
            return {
                "Items": [
                    {
                        "merchant_name": "Walmart",
                        "costs": {"total_usd": 0.0001},
                        "coverage": {"coverage_percentage": 95.2},
                        "gpt_used": False
                    },
                    {
                        "merchant_name": "Target", 
                        "costs": {"total_usd": 0.003},
                        "coverage": {"coverage_percentage": 72.1},
                        "gpt_used": True
                    }
                ]
            }
        elif "PATTERN_STATS#" in pk:
            return {
                "Items": [
                    {
                        "pattern_type": "TC#",
                        "success_rate": 0.998,
                        "usage_count": 1523
                    },
                    {
                        "pattern_type": "DATE_SLASH",
                        "success_rate": 0.952,
                        "usage_count": 891
                    }
                ]
            }
        elif "GPT_USAGE#" in pk:
            return {
                "Items": [
                    {
                        "trigger": {
                            "missing_essentials": ["GRAND_TOTAL"],
                            "coverage_before": 73.2
                        },
                        "response": {
                            "cost_usd": 0.003,
                            "found_labels": {"GRAND_TOTAL": {"confidence": 0.85}}
                        }
                    }
                ]
            }
        
        return {"Items": []}


async def analyze_daily_costs(dynamo_client, date: str):
    """Analyze costs for a specific date."""
    print(f"\n=== Daily Cost Analysis for {date} ===")
    
    # Query workflow records
    response = await dynamo_client.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
        ExpressionAttributeValues={
            ":pk": f"AUDIT#{date}",
            ":sk_prefix": "WORKFLOW#"
        }
    )
    
    # Aggregate by merchant
    costs_by_merchant = defaultdict(lambda: {"total": 0, "count": 0, "gpt_count": 0})
    
    for item in response["Items"]:
        merchant = item["merchant_name"]
        costs_by_merchant[merchant]["total"] += item["costs"]["total_usd"]
        costs_by_merchant[merchant]["count"] += 1
        if item["gpt_used"]:
            costs_by_merchant[merchant]["gpt_count"] += 1
    
    # Display results
    total_cost = 0
    total_receipts = 0
    
    for merchant, stats in sorted(costs_by_merchant.items()):
        avg_cost = stats["total"] / stats["count"]
        gpt_rate = stats["gpt_count"] / stats["count"] * 100
        
        print(f"\n{merchant}:")
        print(f"  Receipts: {stats['count']}")
        print(f"  Total cost: ${stats['total']:.4f}")
        print(f"  Avg cost: ${avg_cost:.4f}")
        print(f"  GPT usage: {gpt_rate:.1f}%")
        
        total_cost += stats["total"]
        total_receipts += stats["count"]
    
    if total_receipts > 0:
        print(f"\n=== TOTAL ===")
        print(f"Receipts: {total_receipts}")
        print(f"Total cost: ${total_cost:.4f}")
        print(f"Average: ${total_cost/total_receipts:.4f}")
        
        # Extrapolate monthly
        monthly_estimate = total_cost * 30
        receipts_per_month = total_receipts * 30
        print(f"\nMonthly projection:")
        print(f"  ~{receipts_per_month:,} receipts")
        print(f"  ~${monthly_estimate:.2f}")


async def find_optimization_opportunities(dynamo_client):
    """Find patterns in GPT usage to optimize."""
    print("\n=== GPT Usage Analysis ===")
    
    # Query GPT usage for current month
    current_month = datetime.now().strftime('%Y-%m')
    response = await dynamo_client.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={
            ":pk": f"GPT_USAGE#{current_month}"
        }
    )
    
    # Analyze triggers
    trigger_counts = defaultdict(int)
    trigger_costs = defaultdict(float)
    
    for item in response["Items"]:
        missing_fields = item["trigger"]["missing_essentials"]
        cost = item["response"]["cost_usd"]
        
        for field in missing_fields:
            trigger_counts[field] += 1
            trigger_costs[field] += cost
    
    # Sort by frequency
    print("\nMost common missing fields:")
    for field, count in sorted(trigger_counts.items(), key=lambda x: x[1], reverse=True):
        avg_cost = trigger_costs[field] / count
        total_cost = trigger_costs[field]
        print(f"  {field}: {count} times (${total_cost:.2f} total, ${avg_cost:.4f} avg)")
    
    # Recommendations
    print("\n=== Optimization Recommendations ===")
    most_common = max(trigger_counts.items(), key=lambda x: x[1])[0] if trigger_counts else None
    
    if most_common:
        print(f"1. Improve pattern detection for '{most_common}'")
        print(f"   Potential savings: ${trigger_costs[most_common]:.2f}/month")
    
    print("2. Add merchant-specific patterns for high-GPT-usage merchants")
    print("3. Implement spatial heuristics for commonly missing fields")


async def analyze_pattern_effectiveness(dynamo_client, merchant: str):
    """Analyze which patterns work best for a merchant."""
    print(f"\n=== Pattern Effectiveness for {merchant} ===")
    
    response = await dynamo_client.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={
            ":pk": f"PATTERN_STATS#{merchant}"
        }
    )
    
    # Sort by success rate
    patterns = sorted(
        response["Items"], 
        key=lambda x: x["success_rate"], 
        reverse=True
    )
    
    print("\nTop performing patterns:")
    for pattern in patterns[:5]:
        print(f"  {pattern['pattern_type']}:")
        print(f"    Success rate: {pattern['success_rate']*100:.1f}%")
        print(f"    Used: {pattern['usage_count']} times")
    
    # Find improvement opportunities
    print("\nPatterns needing improvement:")
    for pattern in patterns:
        if pattern["success_rate"] < 0.9:
            print(f"  {pattern['pattern_type']}: {pattern['success_rate']*100:.1f}% success")


async def estimate_cost_savings():
    """Estimate cost savings from optimization."""
    print("\n=== Cost Savings Calculator ===")
    
    # Assumptions
    receipts_per_day = 1000
    current_gpt_rate = 0.056  # 5.6% need GPT
    current_gpt_cost = 0.003  # $0.003 per GPT call
    
    # Current costs
    current_daily_gpt_cost = receipts_per_day * current_gpt_rate * current_gpt_cost
    current_monthly_gpt_cost = current_daily_gpt_cost * 30
    
    print(f"Current GPT usage: {current_gpt_rate*100:.1f}%")
    print(f"Current monthly GPT cost: ${current_monthly_gpt_cost:.2f}")
    
    # Optimization scenarios
    scenarios = [
        ("5% improvement", 0.051),
        ("10% improvement", 0.046),
        ("Perfect patterns", 0.01),
    ]
    
    print("\nOptimization scenarios:")
    for name, new_rate in scenarios:
        new_monthly_cost = receipts_per_day * new_rate * current_gpt_cost * 30
        savings = current_monthly_gpt_cost - new_monthly_cost
        
        print(f"\n{name} (GPT rate: {new_rate*100:.1f}%):")
        print(f"  Monthly cost: ${new_monthly_cost:.2f}")
        print(f"  Monthly savings: ${savings:.2f}")
        print(f"  Annual savings: ${savings*12:.2f}")


async def main():
    """Run all audit trail analyses."""
    dynamo_client = MockDynamoClient()
    
    # Daily cost analysis
    await analyze_daily_costs(dynamo_client, "2024-01-15")
    
    # GPT optimization opportunities
    await find_optimization_opportunities(dynamo_client)
    
    # Pattern effectiveness
    await analyze_pattern_effectiveness(dynamo_client, "Walmart")
    
    # Cost savings estimates
    await estimate_cost_savings()


if __name__ == "__main__":
    asyncio.run(main())
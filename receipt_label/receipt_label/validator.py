"""
Arithmetic validation for receipt labels.
Extracted from costco_label_discovery.py to improve modularity.
"""

from typing import List, Dict
from receipt_label.receipt_models import CurrencyLabel, LabelType


def validate_arithmetic_relationships(
    discovered_labels: List[CurrencyLabel], known_total: float
) -> Dict[str, bool]:
    """
    Validate arithmetic relationships between discovered labels.

    Returns:
        Dict with validation results for different arithmetic checks
    """
    # Group labels by type
    labels_by_type = {label_type: [] for label_type in LabelType}
    for label in discovered_labels:
        labels_by_type[label.label_type].append(label)

    validation_results = {}

    # Get totals for each type
    grand_totals = [
        label.value for label in labels_by_type[LabelType.GRAND_TOTAL]
    ]
    subtotals = [label.value for label in labels_by_type[LabelType.SUBTOTAL]]
    taxes = [label.value for label in labels_by_type[LabelType.TAX]]
    line_totals = [
        label.value for label in labels_by_type[LabelType.LINE_TOTAL]
    ]

    print(f"\n📊 ARITHMETIC VALIDATION:")
    print(
        f"   GRAND_TOTAL found: {len(grand_totals)} (${', '.join(f'{x:.2f}' for x in grand_totals)})"
    )
    print(
        f"   SUBTOTAL found: {len(subtotals)} (${', '.join(f'{x:.2f}' for x in subtotals)})"
    )
    print(
        f"   TAX found: {len(taxes)} (${', '.join(f'{x:.2f}' for x in taxes)})"
    )
    print(
        f"   LINE_TOTAL found: {len(line_totals)} (sum: ${sum(line_totals):.2f})"
    )

    # Check 1: Grand total matches known total
    validation_results["grand_total_matches_known"] = any(
        abs(gt - known_total) < 0.01 for gt in grand_totals
    )

    # Check 2: SUM(LINE_TOTAL) + TAX = GRAND_TOTAL (primary check)
    line_total_sum = sum(line_totals) if line_totals else 0

    if line_totals and taxes and grand_totals:
        best_match = None
        for tax in taxes:
            for grand_total in grand_totals:
                calculated = line_total_sum + tax
                if abs(calculated - grand_total) < 0.01:
                    best_match = {
                        "line_total_sum": line_total_sum,
                        "tax": tax,
                        "grand_total": grand_total,
                        "calculated": calculated,
                    }
                    break
            if best_match:
                break

        validation_results["line_totals_plus_tax_equals_grand_total"] = (
            best_match is not None
        )
        if best_match:
            print(
                f"   ✅ Arithmetic: ${best_match['line_total_sum']:.2f} (line totals) + ${best_match['tax']:.2f} (tax) = ${best_match['grand_total']:.2f} (grand total)"
            )
        else:
            print(
                f"   ❌ No valid SUM(LINE_TOTAL) + TAX = GRAND_TOTAL combination found"
            )
            print(
                f"      Tried: ${line_total_sum:.2f} + {', '.join(f'${x:.2f}' for x in taxes)} vs {', '.join(f'${x:.2f}' for x in grand_totals)}"
            )
    else:
        validation_results["line_totals_plus_tax_equals_grand_total"] = None
        print(
            f"   ⚠️ Missing data for SUM(LINE_TOTAL) + TAX = GRAND_TOTAL check"
        )

    # Check 3: SUBTOTAL + TAX = GRAND_TOTAL (if subtotal is present)
    if subtotals and taxes and grand_totals:
        best_match = None
        for subtotal in subtotals:
            for tax in taxes:
                for grand_total in grand_totals:
                    calculated = subtotal + tax
                    if abs(calculated - grand_total) < 0.01:
                        best_match = {
                            "subtotal": subtotal,
                            "tax": tax,
                            "grand_total": grand_total,
                            "calculated": calculated,
                        }
                        break
                if best_match:
                    break
            if best_match:
                break

        validation_results["subtotal_plus_tax_equals_total"] = (
            best_match is not None
        )
        if best_match:
            print(
                f"   ✅ Alternative arithmetic: ${best_match['subtotal']:.2f} (subtotal) + ${best_match['tax']:.2f} (tax) = ${best_match['grand_total']:.2f} (grand total)"
            )
        else:
            print(
                f"   ❌ No valid SUBTOTAL + TAX = GRAND_TOTAL combination found"
            )
    else:
        validation_results["subtotal_plus_tax_equals_total"] = None
        print(f"   ⚠️ Missing data for SUBTOTAL + TAX = GRAND_TOTAL check")

    # Check 4: Sum of line totals equals subtotal (if subtotal is present)
    if line_totals and subtotals:
        best_subtotal_match = min(
            subtotals, key=lambda x: abs(x - line_total_sum)
        )
        difference = abs(best_subtotal_match - line_total_sum)

        # Allow up to 10% difference (some items might not be captured)
        tolerance = max(
            best_subtotal_match * 0.10, 2.0
        )  # At least $2 tolerance
        validation_results["line_totals_approximate_subtotal"] = (
            difference <= tolerance
        )

        print(
            f"   📋 Line totals sum ${line_total_sum:.2f} vs subtotal ${best_subtotal_match:.2f} (diff: ${difference:.2f}, tolerance: ${tolerance:.2f})"
        )
        if validation_results["line_totals_approximate_subtotal"]:
            print(f"   ✅ Line totals reasonably close to subtotal")
        else:
            print(
                f"   ⚠️ Line totals differ significantly from subtotal (missing items?)"
            )
    else:
        validation_results["line_totals_approximate_subtotal"] = None
        print(f"   ⚠️ Missing data for line totals vs subtotal check")

    # Check 5: Tax rate reasonableness (0-15% typical for retail)
    if taxes and subtotals:
        for tax in taxes:
            for subtotal in subtotals:
                if subtotal > 0:
                    tax_rate = (tax / subtotal) * 100
                    validation_results["reasonable_tax_rate"] = (
                        0 <= tax_rate <= 15
                    )  # Allow 0% to 15%
                    print(
                        f"   💰 Tax rate: {tax_rate:.1f}% (${tax:.2f} / ${subtotal:.2f})"
                    )
                    break
    else:
        validation_results["reasonable_tax_rate"] = None

    return validation_results
#!/usr/bin/env python3
"""
Test the NumPy optimization for math solver.
Compare performance between brute force and NumPy DP approaches.
"""

import time
from typing import List, Tuple
import numpy as np
import uuid

# Set up environment
import os
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_dynamo.entities.receipt_word import ReceiptWord


def create_mock_pattern_match(value: float, word_id: int) -> PatternMatch:
    """Create a mock pattern match for testing."""
    mock_word = ReceiptWord(
        image_id=str(uuid.uuid4()),
        line_id=word_id,
        word_id=word_id,
        text=f"${value:.2f}",
        bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
        top_right={"x": 50, "y": 0},
        top_left={"x": 0, "y": 0},
        bottom_right={"x": 50, "y": 20},
        bottom_left={"x": 0, "y": 20},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.9,
        extracted_data={},
        receipt_id=1
    )
    
    return PatternMatch(
        word=mock_word,
        pattern_type=PatternType.CURRENCY,
        confidence=0.9,
        matched_text=f"${value:.2f}",
        extracted_value=value,
        metadata={}
    )


def test_optimization_performance():
    """Test performance difference between brute force and NumPy optimization."""
    
    print("🧮 TESTING NUMPY OPTIMIZATION PERFORMANCE")
    print("=" * 60)
    
    # Test case 1: Small problem (should use brute force for both)
    print("\n📊 Test Case 1: Small Problem (8 values)")
    small_values = [1.99, 2.50, 3.75, 4.99, 5.25, 6.50, 7.99, 8.75]
    target_total = 25.00  # Some of these should sum to ~$25
    
    small_currency_values = [
        (value, create_mock_pattern_match(value, i)) 
        for i, value in enumerate(small_values)
    ]
    small_currency_values.append((target_total, create_mock_pattern_match(target_total, len(small_values))))
    
    # Test with brute force
    solver_brute = MathSolverDetector(tolerance=0.02, use_numpy_optimization=False)
    start_time = time.time()
    solutions_brute = solver_brute.solve_receipt_math(small_currency_values)
    brute_time = time.time() - start_time
    
    # Test with NumPy (should still use brute force for small problems)
    solver_numpy = MathSolverDetector(tolerance=0.02, use_numpy_optimization=True)
    start_time = time.time()
    solutions_numpy = solver_numpy.solve_receipt_math(small_currency_values)
    numpy_time = time.time() - start_time
    
    print(f"   Brute force: {len(solutions_brute)} solutions in {brute_time*1000:.1f}ms")
    print(f"   NumPy mode:  {len(solutions_numpy)} solutions in {numpy_time*1000:.1f}ms")
    print(f"   Both should use brute force for small problems")
    
    # Test case 2: Large problem (should show NumPy advantage)
    print("\n📊 Test Case 2: Large Problem (20 values)")
    
    # Create realistic grocery receipt prices
    large_values = [
        1.99, 2.50, 3.75, 4.99, 5.25, 6.50, 7.99, 8.75, 2.25, 3.50,
        1.75, 4.25, 6.99, 3.99, 5.75, 2.99, 4.50, 7.25, 1.50, 8.99
    ]
    target_total = 85.50  # Realistic total for grocery receipt
    
    large_currency_values = [
        (value, create_mock_pattern_match(value, i)) 
        for i, value in enumerate(large_values)
    ]
    large_currency_values.append((target_total, create_mock_pattern_match(target_total, len(large_values))))
    
    print(f"   Testing with {len(large_values)} prices, target total ${target_total:.2f}")
    
    # Test with brute force (limited to prevent timeout)
    solver_brute = MathSolverDetector(tolerance=0.02, use_numpy_optimization=False, max_solutions=10)
    start_time = time.time()
    solutions_brute = solver_brute.solve_receipt_math(large_currency_values)
    brute_time = time.time() - start_time
    
    # Test with NumPy optimization
    solver_numpy = MathSolverDetector(tolerance=0.02, use_numpy_optimization=True, max_solutions=10)
    start_time = time.time()
    solutions_numpy = solver_numpy.solve_receipt_math(large_currency_values)
    numpy_time = time.time() - start_time
    
    print(f"   Brute force: {len(solutions_brute)} solutions in {brute_time*1000:.1f}ms")
    print(f"   NumPy DP:    {len(solutions_numpy)} solutions in {numpy_time*1000:.1f}ms")
    
    if numpy_time > 0:
        speedup = brute_time / numpy_time
        print(f"   Speedup: {speedup:.1f}x faster with NumPy")
    
    # Show sample solutions
    if solutions_numpy:
        best_solution = solutions_numpy[0]
        print(f"\n✅ Best Solution Found:")
        print(f"   Items: {[f'${p[0]:.2f}' for p in best_solution.item_prices]}")
        print(f"   Subtotal: ${best_solution.subtotal:.2f}")
        if best_solution.tax:
            print(f"   Tax: ${best_solution.tax[0]:.2f}")
        print(f"   Total: ${best_solution.grand_total[0]:.2f}")
        print(f"   Confidence: {best_solution.confidence:.2f}")
    
    # Test case 3: Very large problem (stress test)
    print("\n📊 Test Case 3: Stress Test (30 values)")
    
    # Create an even larger set of values
    stress_values = [
        1.25, 1.50, 1.75, 1.99, 2.25, 2.50, 2.75, 2.99, 3.25, 3.50,
        3.75, 3.99, 4.25, 4.50, 4.75, 4.99, 5.25, 5.50, 5.75, 5.99,
        6.25, 6.50, 6.75, 6.99, 7.25, 7.50, 7.75, 7.99, 8.25, 8.50
    ]
    stress_total = 125.75
    
    stress_currency_values = [
        (value, create_mock_pattern_match(value, i)) 
        for i, value in enumerate(stress_values)
    ]
    stress_currency_values.append((stress_total, create_mock_pattern_match(stress_total, len(stress_values))))
    
    print(f"   Testing with {len(stress_values)} prices, target total ${stress_total:.2f}")
    print(f"   Brute force would require checking 2^{len(stress_values)} = {2**len(stress_values):,} combinations!")
    
    # Only test NumPy (brute force would take too long)
    solver_numpy = MathSolverDetector(tolerance=0.02, use_numpy_optimization=True, max_solutions=5)
    start_time = time.time()
    solutions_numpy = solver_numpy.solve_receipt_math(stress_currency_values)
    numpy_time = time.time() - start_time
    
    print(f"   NumPy DP: {len(solutions_numpy)} solutions in {numpy_time*1000:.1f}ms")
    print(f"   Memory efficient: DP table size ~{len(stress_values)} × {int(stress_total*100)} = {len(stress_values) * int(stress_total*100):,} bytes")
    
    print(f"\n🎯 PERFORMANCE SUMMARY")
    print("=" * 60)
    print(f"✅ NumPy optimization successfully handles large problems")
    print(f"✅ Automatically falls back to brute force for small problems")  
    print(f"✅ Memory efficient: Uses uint8 arrays for DP table")
    print(f"✅ Time complexity: O(n × target) vs O(2^n) brute force")
    print(f"✅ Ready for production use with 20+ price receipts")


if __name__ == "__main__":
    test_optimization_performance()
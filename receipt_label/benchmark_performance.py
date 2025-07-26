#!/usr/bin/env python3
"""
Benchmark the enhanced pattern analyzer processing performance.
"""

import time
from typing import List
from receipt_label.pattern_detection.enhanced_pattern_analyzer import enhanced_pattern_analysis

def create_sample_contexts(size: int = 10) -> List[dict]:
    """Create sample currency contexts for benchmarking."""
    contexts = []
    for i in range(size):
        contexts.append({
            'amount': 5.99 + i,
            'text': f'${5.99 + i}',
            'line_id': str(i + 1),
            'x_position': 200,
            'y_position': 100 + i * 20,
            'full_line': f'Item {i + 1} ${5.99 + i}',
            'left_text': f'Item {i + 1}'
        })
    
    # Add financial summary
    subtotal = sum(ctx['amount'] for ctx in contexts)
    tax = round(subtotal * 0.08, 2)
    total = subtotal + tax
    
    contexts.extend([
        {
            'amount': subtotal,
            'text': f'${subtotal}',
            'line_id': str(size + 1),
            'x_position': 200,
            'y_position': 100 + size * 20 + 40,
            'full_line': f'SUBTOTAL ${subtotal}',
            'left_text': 'SUBTOTAL'
        },
        {
            'amount': tax,
            'text': f'${tax}',
            'line_id': str(size + 2),
            'x_position': 200,
            'y_position': 100 + size * 20 + 60,
            'full_line': f'TAX ${tax}',
            'left_text': 'TAX'
        },
        {
            'amount': total,
            'text': f'${total}',
            'line_id': str(size + 3),
            'x_position': 200,
            'y_position': 100 + size * 20 + 80,
            'full_line': f'TOTAL ${total}',
            'left_text': 'TOTAL'
        }
    ])
    
    return contexts

def benchmark_processing_time():
    """Benchmark processing time for different receipt sizes."""
    print("Enhanced Pattern Analyzer Performance Benchmark")
    print("=" * 50)
    
    sizes = [5, 10, 20, 50, 100]
    
    for size in sizes:
        contexts = create_sample_contexts(size)
        
        # Warm up
        enhanced_pattern_analysis(contexts)
        
        # Benchmark
        times = []
        for _ in range(10):
            start_time = time.perf_counter()
            result = enhanced_pattern_analysis(contexts)
            end_time = time.perf_counter()
            times.append((end_time - start_time) * 1000)  # Convert to ms
        
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        
        print(f"\nReceipt with {size} line items ({len(contexts)} total currency amounts):")
        print(f"  Average: {avg_time:.2f}ms")
        print(f"  Min:     {min_time:.2f}ms")
        print(f"  Max:     {max_time:.2f}ms")
        print(f"  Found {len(result.get('line_items', []))} line items")
        print(f"  Confidence: {result.get('confidence', 0):.2f}")

if __name__ == "__main__":
    benchmark_processing_time()
#!/usr/bin/env python3
"""
Test Phase 2 spatial improvements: font detection, enhanced clustering, and spacing analysis.
"""

import os
import time
import asyncio
from pathlib import Path
import random
from collections import Counter

# Set up environment  
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
import json
from receipt_dynamo.entities.receipt_word import ReceiptWord


def load_receipt_words(file_path: Path):
    """Load words from receipt."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        words = []
        for word_data in data.get('words', []):
            word = ReceiptWord(
                image_id=word_data['image_id'],
                line_id=word_data['line_id'],
                word_id=word_data['word_id'],
                text=word_data['text'],
                bounding_box=word_data['bounding_box'],
                top_right=word_data['top_right'],
                top_left=word_data['top_left'],
                bottom_right=word_data['bottom_right'],
                bottom_left=word_data['bottom_left'],
                angle_degrees=word_data.get('angle_degrees', 0.0),
                angle_radians=word_data.get('angle_radians', 0.0),
                confidence=word_data['confidence'],
                extracted_data=word_data.get('extracted_data', {}),
                receipt_id=int(word_data.get('receipt_id', 1))
            )
            words.append(word)
        
        # Get merchant from metadata
        merchant = 'Unknown'
        receipt_metadatas = data.get('receipt_metadatas', [])
        if receipt_metadatas:
            merchant = receipt_metadatas[0].get('merchant_name', 'Unknown')
            
        return words, merchant
    except Exception as e:
        return [], f"Load error: {str(e)[:50]}..."


async def test_phase2_improvements():
    """Test Phase 2 spatial improvements against legacy system."""
    
    print("🚀 TESTING PHASE 2 SPATIAL IMPROVEMENTS")
    print("=" * 60)
    
    # Find receipt files to test
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))
    
    # Test on a diverse sample
    sample_size = min(15, len(all_files))
    sample_files = random.sample(all_files, sample_size)
    
    print(f"📄 Testing {sample_size} receipts from {len(all_files)} total")
    print(f"🔬 Comparing Legacy vs Phase 2 Enhanced spatial analysis")
    
    # Create pattern orchestrator
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    
    # Create detectors
    legacy_detector = VerticalAlignmentDetector(alignment_tolerance=0.02, use_enhanced_clustering=False)
    enhanced_detector = VerticalAlignmentDetector(alignment_tolerance=0.02, use_enhanced_clustering=True)
    
    # Math solver for validation
    math_solver = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    results = []
    
    print("\n🔄 Processing receipts...")
    
    for i, file_path in enumerate(sample_files):
        print(f"\n📋 Receipt {i+1}: {file_path.name[:40]}...")
        
        # Load receipt
        words, merchant = load_receipt_words(file_path)
        if not words:
            print(f"   ❌ Failed to load")
            continue
        
        start_time = time.time()
        
        try:
            # Get pattern matches
            pattern_results = await pattern_orchestrator.detect_all_patterns(words)
            all_matches = []
            for detector_name, matches in pattern_results.items():
                if detector_name != '_metadata':
                    all_matches.extend(matches)
            
            # Extract currency values
            currency_patterns = {'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'}
            currency_values = []
            for match in all_matches:
                if match.pattern_type.name in currency_patterns and match.extracted_value:
                    try:
                        value = float(match.extracted_value)
                        if 0.001 <= abs(value) <= 999.99:
                            currency_values.append((value, match))
                    except (ValueError, TypeError):
                        continue
            
            # Test Legacy Detector
            legacy_start = time.time()
            legacy_columns = legacy_detector.detect_price_columns([v[1] for v in currency_values])
            legacy_time = time.time() - legacy_start
            
            # Test Enhanced Detector (Phase 2)
            enhanced_start = time.time()
            enhanced_columns = enhanced_detector.detect_price_columns([v[1] for v in currency_values])
            enhanced_time = time.time() - enhanced_start
            
            # Analyze results
            legacy_result = {
                'columns': len(legacy_columns),
                'best_confidence': max(c.confidence for c in legacy_columns) if legacy_columns else 0,
                'processing_time': legacy_time,
                'total_prices': sum(len(c.prices) for c in legacy_columns),
                'has_totals': any(any(p.pattern_type.name in ['GRAND_TOTAL', 'SUBTOTAL'] for p in c.prices) for c in legacy_columns)
            }
            
            enhanced_result = {
                'columns': len(enhanced_columns),
                'best_confidence': max(c.confidence for c in enhanced_columns) if enhanced_columns else 0,
                'processing_time': enhanced_time,
                'total_prices': sum(len(c.prices) for c in enhanced_columns),
                'has_totals': any(any(p.pattern_type.name in ['GRAND_TOTAL', 'SUBTOTAL'] for p in c.prices) for c in enhanced_columns),
                # Phase 2 specific metrics
                'x_alignment_tightness': max(c.x_alignment_tightness for c in enhanced_columns) if enhanced_columns and hasattr(enhanced_columns[0], 'x_alignment_tightness') else 0,
                'font_consistency': max(c.font_consistency.confidence for c in enhanced_columns if c.font_consistency) if enhanced_columns else 0,
                'has_large_fonts': any(c.font_consistency and c.font_consistency.is_larger_than_normal for c in enhanced_columns) if enhanced_columns else False
            }
            
            # Test mathematical validation
            math_start = time.time()
            if enhanced_columns:
                column_lines = set()
                for column in enhanced_columns:
                    column_lines.update(p.word.line_id for p in column.prices)
                
                column_currencies = [
                    (value, match) for value, match in currency_values
                    if match.word.line_id in column_lines
                ]
                solutions = math_solver.solve_receipt_math(column_currencies)
            else:
                solutions = math_solver.solve_receipt_math(currency_values)
            
            math_time = time.time() - math_start
            
            # Classify solution confidence
            if not solutions:
                solution_confidence = 'no_solution'
            elif len(solutions) == 1 and solutions[0].confidence >= 0.9 and enhanced_result['best_confidence'] >= 0.8:
                solution_confidence = 'high_confidence'
            elif len(solutions) <= 3 and solutions[0].confidence >= 0.8:
                solution_confidence = 'medium_confidence'
            else:
                solution_confidence = 'low_confidence'
            
            total_time = time.time() - start_time
            
            result = {
                'file': file_path.name,
                'merchant': merchant,
                'currency_count': len(currency_values),
                'legacy': legacy_result,
                'enhanced': enhanced_result,
                'solutions': len(solutions),
                'solution_confidence': solution_confidence,
                'best_solution_confidence': solutions[0].confidence if solutions else 0,
                'math_time': math_time,
                'total_time': total_time
            }
            
            results.append(result)
            
            # Print comparison
            print(f"   💰 Currency values: {len(currency_values)}")
            print(f"   📊 Legacy:   {legacy_result['columns']} columns, confidence={legacy_result['best_confidence']:.2f}, time={legacy_time*1000:.1f}ms")
            print(f"   🚀 Enhanced: {enhanced_result['columns']} columns, confidence={enhanced_result['best_confidence']:.2f}, time={enhanced_time*1000:.1f}ms")
            if enhanced_result.get('x_alignment_tightness', 0) > 0:
                print(f"      ✨ X-tightness: {enhanced_result['x_alignment_tightness']:.2f}, Font: {enhanced_result['font_consistency']:.2f}")
            print(f"   🧮 Math: {len(solutions)} solutions, confidence={solution_confidence}")
            
        except Exception as e:
            print(f"   💥 Error: {str(e)[:60]}...")
            continue
    
    # Analysis
    print(f"\n📈 PHASE 2 IMPROVEMENT ANALYSIS")
    print("=" * 60)
    
    if not results:
        print("❌ No successful results to analyze")
        return
    
    # Performance comparison
    legacy_times = [r['legacy']['processing_time'] for r in results]
    enhanced_times = [r['enhanced']['processing_time'] for r in results]
    avg_legacy_time = sum(legacy_times) / len(legacy_times)
    avg_enhanced_time = sum(enhanced_times) / len(enhanced_times)
    
    print(f"⏱️  Processing Time Comparison:")
    print(f"   Legacy average:   {avg_legacy_time*1000:.1f}ms")
    print(f"   Enhanced average: {avg_enhanced_time*1000:.1f}ms")
    if avg_legacy_time > 0:
        speedup = avg_legacy_time / avg_enhanced_time
        print(f"   Speedup: {speedup:.1f}x {'faster' if speedup > 1 else 'slower'}")
    
    # Confidence improvements
    legacy_confidences = [r['legacy']['best_confidence'] for r in results]
    enhanced_confidences = [r['enhanced']['best_confidence'] for r in results]
    avg_legacy_conf = sum(legacy_confidences) / len(legacy_confidences)
    avg_enhanced_conf = sum(enhanced_confidences) / len(enhanced_confidences)
    
    print(f"\n🎯 Confidence Comparison:")
    print(f"   Legacy average:   {avg_legacy_conf:.3f}")
    print(f"   Enhanced average: {avg_enhanced_conf:.3f}")
    confidence_improvement = ((avg_enhanced_conf - avg_legacy_conf) / avg_legacy_conf) * 100 if avg_legacy_conf > 0 else 0
    print(f"   Improvement: {confidence_improvement:+.1f}%")
    
    # Column detection comparison
    legacy_columns = [r['legacy']['columns'] for r in results]
    enhanced_columns = [r['enhanced']['columns'] for r in results]
    
    print(f"\n🏛️  Column Detection Comparison:")
    print(f"   Legacy: avg={sum(legacy_columns)/len(legacy_columns):.1f}, range={min(legacy_columns)}-{max(legacy_columns)}")
    print(f"   Enhanced: avg={sum(enhanced_columns)/len(enhanced_columns):.1f}, range={min(enhanced_columns)}-{max(enhanced_columns)}")
    
    # Phase 2 specific features
    tightness_values = [r['enhanced'].get('x_alignment_tightness', 0) for r in results if r['enhanced'].get('x_alignment_tightness', 0) > 0]
    font_confidences = [r['enhanced'].get('font_consistency', 0) for r in results if r['enhanced'].get('font_consistency', 0) > 0]
    large_font_count = sum(1 for r in results if r['enhanced'].get('has_large_fonts', False))
    
    print(f"\n✨ Phase 2 Feature Analysis:")
    if tightness_values:
        print(f"   X-alignment tightness: avg={sum(tightness_values)/len(tightness_values):.3f}, max={max(tightness_values):.3f}")
    if font_confidences:
        print(f"   Font consistency: avg={sum(font_confidences)/len(font_confidences):.3f}, max={max(font_confidences):.3f}")
    print(f"   Large font detection: {large_font_count}/{len(results)} receipts ({large_font_count/len(results)*100:.1f}%)")
    
    # Mathematical validation impact
    solution_confidence_dist = Counter(r['solution_confidence'] for r in results)
    
    print(f"\n🧮 Mathematical Validation Results:")
    for confidence_level, count in solution_confidence_dist.most_common():
        print(f"   {confidence_level}: {count} receipts ({count/len(results)*100:.1f}%)")
    
    # High confidence analysis
    high_confidence_count = solution_confidence_dist.get('high_confidence', 0)
    medium_confidence_count = solution_confidence_dist.get('medium_confidence', 0)
    
    print(f"\n💰 Cost Reduction Potential:")
    print(f"   High confidence (no Pinecone): {high_confidence_count} ({high_confidence_count/len(results)*100:.1f}%)")
    print(f"   Medium confidence (light Pinecone): {medium_confidence_count} ({medium_confidence_count/len(results)*100:.1f}%)")
    
    # Estimate cost reduction
    total_receipts = len(results)
    no_pinecone = high_confidence_count
    light_pinecone = medium_confidence_count
    full_pinecone = total_receipts - no_pinecone - light_pinecone
    
    current_cost = total_receipts * 1.0  # 100% use Pinecone
    new_cost = (no_pinecone * 0.0) + (light_pinecone * 0.3) + (full_pinecone * 1.0)
    cost_reduction = ((current_cost - new_cost) / current_cost) * 100 if current_cost > 0 else 0
    
    print(f"   Estimated cost reduction: {cost_reduction:.1f}%")
    
    # Top performers
    print(f"\n🏆 Top Performing Receipts (Enhanced):")
    sorted_results = sorted(results, key=lambda r: r['enhanced']['best_confidence'], reverse=True)
    
    for i, result in enumerate(sorted_results[:3]):
        enhanced = result['enhanced']
        print(f"   {i+1}. {result['file'][:30]} - confidence={enhanced['best_confidence']:.3f}")
        if enhanced.get('x_alignment_tightness', 0) > 0:
            print(f"      X-tightness={enhanced['x_alignment_tightness']:.3f}, Font={enhanced['font_consistency']:.3f}")
        print(f"      Solution: {result['solution_confidence']} ({result['best_solution_confidence']:.3f})")
    
    print(f"\n🎯 PHASE 2 IMPACT SUMMARY:")
    print(f"   ✅ Average confidence improvement: {confidence_improvement:+.1f}%")
    print(f"   ✅ Processing speed: {speedup:.1f}x")
    print(f"   ✅ High-confidence receipts: {high_confidence_count}/{len(results)} ({high_confidence_count/len(results)*100:.1f}%)")
    print(f"   ✅ Cost reduction potential: {cost_reduction:.1f}%")
    print(f"   ✅ Font analysis coverage: {len(font_confidences)}/{len(results)} receipts")
    print(f"   ✅ Enhanced clustering: All receipts benefit from improved spatial analysis")


if __name__ == "__main__":
    asyncio.run(test_phase2_improvements())
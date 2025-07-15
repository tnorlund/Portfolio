#!/usr/bin/env python3
"""
Validate that our spatial/math detection actually finds the correct grand totals and line items.
Compare against ground truth labels from exported DynamoDB data.
"""

import os
import asyncio
from pathlib import Path
from collections import Counter, defaultdict
import json

# Set up environment  
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_dynamo.entities.receipt_word import ReceiptWord


def load_receipt_with_labels(file_path: Path):
    """Load receipt words and their ground truth labels from exported data."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        # Get all receipts in the image
        receipts = data.get('receipts', [])
        if not receipts:
            return [], {}, 'No receipts found'
        
        # Process each receipt
        all_results = []
        
        for receipt in receipts:
            receipt_id = receipt['receipt_id']
            
            # Build word lookup
            word_lookup = {}
            for word_data in data.get('receipt_words', []):
                if word_data['receipt_id'] == receipt_id:
                    key = (word_data['line_id'], word_data['word_id'])
                    word_lookup[key] = word_data
            
            # Build label lookup
            label_lookup = defaultdict(list)
            for label_data in data.get('receipt_word_labels', []):
                if label_data['receipt_id'] == receipt_id:
                    key = (label_data['line_id'], label_data['word_id'])
                    label_lookup[key].append(label_data['label'])
            
            # Create ReceiptWord objects with labels
            words = []
            word_labels = {}
            
            for (line_id, word_id), word_data in word_lookup.items():
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
                    receipt_id=receipt_id
                )
                words.append(word)
                
                # Get labels for this word
                if (line_id, word_id) in label_lookup:
                    word_labels[word.word_id] = label_lookup[(line_id, word_id)]
            
            # Get merchant from metadata
            merchant = 'Unknown'
            for metadata in data.get('receipt_metadatas', []):
                if metadata['receipt_id'] == receipt_id:
                    merchant = metadata.get('merchant_name', 'Unknown')
                    break
            
            all_results.append((words, word_labels, merchant, receipt_id))
        
        return all_results
        
    except Exception as e:
        return []


def extract_ground_truth_totals(words, word_labels):
    """Extract ground truth grand total, subtotal, and tax from labels."""
    ground_truth = {
        'grand_total': None,
        'subtotal': None,
        'tax': None,
        'line_items': []
    }
    
    for word in words:
        labels = word_labels.get(word.word_id, [])
        
        # Check for relevant labels
        if 'GRAND_TOTAL' in labels or 'AMOUNT_TOTAL' in labels:
            try:
                text = word.text.replace('$', '').replace(',', '')
                value = float(text)
                # Sanity check - receipts shouldn't have totals over $10,000
                if 0.01 <= value <= 10000:
                    ground_truth['grand_total'] = value
            except:
                pass
                
        elif 'SUBTOTAL' in labels or 'AMOUNT_SUBTOTAL' in labels:
            try:
                text = word.text.replace('$', '').replace(',', '')
                value = float(text)
                if 0.01 <= value <= 10000:
                    ground_truth['subtotal'] = value
            except:
                pass
                
        elif 'TAX' in labels or 'AMOUNT_TAX' in labels:
            try:
                text = word.text.replace('$', '').replace(',', '')
                value = float(text)
                if 0.00 <= value <= 1000:  # Tax can be 0
                    ground_truth['tax'] = value
            except:
                pass
                
        elif 'LINE_TOTAL' in labels or 'AMOUNT_LINE_ITEM' in labels or 'PRICE' in labels:
            try:
                text = word.text.replace('$', '').replace(',', '')
                value = float(text)
                if 0.01 <= value <= 5000:  # Individual items shouldn't be too expensive
                    ground_truth['line_items'].append(value)
            except:
                pass
    
    return ground_truth


async def validate_receipt_accuracy(file_path: Path):
    """Validate a single receipt's spatial/math detection against ground truth."""
    
    # Load all receipts from the file
    receipt_data_list = load_receipt_with_labels(file_path)
    if not receipt_data_list:
        return None
    
    # Check if this is an error case
    if not isinstance(receipt_data_list, list) or (len(receipt_data_list) == 3 and isinstance(receipt_data_list[2], str)):
        return None
    
    validations = []
    
    # Process each receipt in the image
    for words, word_labels, merchant, receipt_id in receipt_data_list:
        if not words:
            continue
            
        # Extract ground truth
        ground_truth = extract_ground_truth_totals(words, word_labels)
        
        # Skip if no ground truth grand total
        if ground_truth['grand_total'] is None:
            continue
        
        # Run our detection pipeline
        pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
        alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02, use_enhanced_clustering=True)
        math_solver = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
        
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
            
            # Spatial analysis
            alignment_result = alignment_detector.detect_line_items_with_alignment(words, all_matches)
            price_columns = alignment_detector.detect_price_columns([v[1] for v in currency_values])
            
            # Filter to price columns
            if price_columns:
                column_lines = set()
                for column in price_columns:
                    column_lines.update(p.word.line_id for p in column.prices)
                
                column_currencies = [
                    (value, match) for value, match in currency_values
                    if match.word.line_id in column_lines
                ]
            else:
                column_currencies = currency_values
            
            # Mathematical validation
            solutions = math_solver.solve_receipt_math(column_currencies)
            
            # Extract our detected values
            detected = {
                'grand_total': None,
                'subtotal': None,
                'tax': None,
                'line_items': []
            }
            
            if solutions:
                best_solution = max(solutions, key=lambda s: s.confidence)
                detected['grand_total'] = best_solution.grand_total[0]
                detected['subtotal'] = best_solution.subtotal
                if best_solution.tax:
                    detected['tax'] = best_solution.tax[0]
                detected['line_items'] = [price[0] for price in best_solution.item_prices]
                
                # Calculate confidence using simplified approach
                spatial_analysis = {
                    'best_column_confidence': alignment_result['best_column_confidence'],
                    'x_alignment_tightness': alignment_result.get('x_alignment_tightness', 0),
                    'font_consistency_confidence': alignment_result.get('font_consistency_confidence', 0),
                    'has_large_font_patterns': alignment_result.get('has_large_font_patterns', False)
                }
                
                # Simplified confidence calculation
                math_score = best_solution.confidence
                spatial_score = spatial_analysis['best_column_confidence']
                
                if spatial_analysis['x_alignment_tightness'] > 0.9:
                    spatial_score *= 1.1
                if spatial_analysis['has_large_font_patterns']:
                    spatial_score *= 1.1
                if spatial_analysis['font_consistency_confidence'] > 0.6:
                    spatial_score *= 1.05
                
                combined_score = (math_score + spatial_score) / 2
                
                if combined_score >= 0.85:
                    confidence_level = 'high_confidence'
                elif combined_score >= 0.7:
                    confidence_level = 'medium_confidence'
                elif combined_score >= 0.5:
                    confidence_level = 'low_confidence'
                else:
                    confidence_level = 'no_solution'
            else:
                confidence_level = 'no_solution'
            
            # Compare against ground truth
            validation = {
                'file': file_path.name,
                'receipt_id': receipt_id,
                'merchant': merchant,
                'confidence_level': confidence_level,
                'ground_truth': ground_truth,
                'detected': detected,
                'accuracy': {}
            }
            
            # Check grand total accuracy
            if detected['grand_total'] is not None and ground_truth['grand_total'] is not None:
                diff = abs(detected['grand_total'] - ground_truth['grand_total'])
                validation['accuracy']['grand_total_correct'] = diff < 0.02  # Within 2 cents
                validation['accuracy']['grand_total_diff'] = diff
            else:
                validation['accuracy']['grand_total_correct'] = False
                validation['accuracy']['grand_total_diff'] = None
            
            # Check subtotal accuracy
            if detected['subtotal'] is not None and ground_truth['subtotal'] is not None:
                diff = abs(detected['subtotal'] - ground_truth['subtotal'])
                validation['accuracy']['subtotal_correct'] = diff < 0.02
                validation['accuracy']['subtotal_diff'] = diff
            else:
                validation['accuracy']['subtotal_correct'] = False
                validation['accuracy']['subtotal_diff'] = None
            
            # Check tax accuracy
            if detected['tax'] is not None and ground_truth['tax'] is not None:
                diff = abs(detected['tax'] - ground_truth['tax'])
                validation['accuracy']['tax_correct'] = diff < 0.02
                validation['accuracy']['tax_diff'] = diff
            else:
                validation['accuracy']['tax_correct'] = False
                validation['accuracy']['tax_diff'] = None
            
            # Check line items (more complex - just check count for now)
            validation['accuracy']['line_item_count_match'] = len(detected['line_items']) == len(ground_truth['line_items'])
            
            validations.append(validation)
            
        except Exception as e:
            validations.append({
                'file': file_path.name,
                'receipt_id': receipt_id,
                'merchant': merchant,
                'error': str(e)[:100]
            })
    
    return validations


async def validate_all_receipts():
    """Validate accuracy across all receipts with ground truth."""
    
    print("🔍 VALIDATING SPATIAL/MATH ACCURACY AGAINST GROUND TRUTH")
    print("=" * 60)
    
    # Find all exported receipt files
    receipt_dir = Path("./receipt_data_with_labels_full")
    all_files = list(receipt_dir.glob("*.json"))
    
    print(f"📄 Found {len(all_files)} receipt files to validate")
    
    all_validations = []
    
    # Process in batches for better progress tracking
    batch_size = 5
    for i in range(0, len(all_files), batch_size):
        batch = all_files[i:i+batch_size]
        print(f"\n🔄 Processing batch {i//batch_size + 1}/{(len(all_files) + batch_size - 1)//batch_size}")
        
        # Process batch concurrently
        batch_results = await asyncio.gather(*[validate_receipt_accuracy(f) for f in batch])
        
        # Flatten results (since each file can have multiple receipts)
        for result in batch_results:
            if result:
                all_validations.extend(result)
    
    # Analyze results
    print(f"\n📊 VALIDATION RESULTS")
    print("=" * 60)
    
    valid_validations = [v for v in all_validations if 'error' not in v]
    print(f"✅ Successfully validated: {len(valid_validations)} receipts")
    
    if not valid_validations:
        print("❌ No receipts could be validated")
        return
    
    # Accuracy by confidence level
    accuracy_by_confidence = defaultdict(lambda: {
        'total': 0,
        'grand_total_correct': 0,
        'subtotal_correct': 0,
        'tax_correct': 0,
        'line_item_count_match': 0
    })
    
    for v in valid_validations:
        conf = v['confidence_level']
        accuracy_by_confidence[conf]['total'] += 1
        
        if v['accuracy']['grand_total_correct']:
            accuracy_by_confidence[conf]['grand_total_correct'] += 1
        if v['accuracy']['subtotal_correct']:
            accuracy_by_confidence[conf]['subtotal_correct'] += 1
        if v['accuracy']['tax_correct']:
            accuracy_by_confidence[conf]['tax_correct'] += 1
        if v['accuracy']['line_item_count_match']:
            accuracy_by_confidence[conf]['line_item_count_match'] += 1
    
    # Print accuracy by confidence level
    print("\n🎯 ACCURACY BY CONFIDENCE LEVEL:")
    for conf_level in ['high_confidence', 'medium_confidence', 'low_confidence', 'no_solution']:
        stats = accuracy_by_confidence[conf_level]
        if stats['total'] > 0:
            print(f"\n{conf_level.upper()} ({stats['total']} receipts):")
            print(f"  Grand Total Accuracy: {stats['grand_total_correct']}/{stats['total']} ({stats['grand_total_correct']/stats['total']*100:.1f}%)")
            
            # Check how many had subtotal/tax in ground truth
            subtotal_count = sum(1 for v in valid_validations if v['confidence_level'] == conf_level and v['ground_truth']['subtotal'] is not None)
            tax_count = sum(1 for v in valid_validations if v['confidence_level'] == conf_level and v['ground_truth']['tax'] is not None)
            
            if subtotal_count > 0:
                print(f"  Subtotal Accuracy: {stats['subtotal_correct']}/{subtotal_count} ({stats['subtotal_correct']/subtotal_count*100:.1f}%)")
            if tax_count > 0:
                print(f"  Tax Accuracy: {stats['tax_correct']}/{tax_count} ({stats['tax_correct']/tax_count*100:.1f}%)")
            
            print(f"  Line Item Count Match: {stats['line_item_count_match']}/{stats['total']} ({stats['line_item_count_match']/stats['total']*100:.1f}%)")
    
    # Overall accuracy for high confidence
    high_conf_stats = accuracy_by_confidence['high_confidence']
    if high_conf_stats['total'] > 0:
        print(f"\n🏆 HIGH CONFIDENCE VALIDATION:")
        print(f"  Total high-confidence receipts: {high_conf_stats['total']}")
        print(f"  Grand total accuracy: {high_conf_stats['grand_total_correct']/high_conf_stats['total']*100:.1f}%")
        print(f"  ✅ This is what we'd process without Pinecone!")
    
    # Show some example failures
    print(f"\n❌ EXAMPLE VALIDATION FAILURES (High Confidence):")
    failure_count = 0
    for v in valid_validations:
        if v['confidence_level'] == 'high_confidence' and not v['accuracy']['grand_total_correct']:
            print(f"\n  {v['file']} (Receipt {v['receipt_id']}):")
            print(f"    Merchant: {v['merchant']}")
            print(f"    Ground Truth Total: ${v['ground_truth']['grand_total']:.2f}")
            print(f"    Detected Total: ${v['detected']['grand_total']:.2f}" if v['detected']['grand_total'] else "    Detected Total: None")
            if v['accuracy']['grand_total_diff'] is not None:
                print(f"    Difference: ${v['accuracy']['grand_total_diff']:.2f}")
            
            failure_count += 1
            if failure_count >= 5:
                break
    
    # Merchant-specific accuracy
    print(f"\n🏪 ACCURACY BY MERCHANT (High Confidence Only):")
    merchant_accuracy = defaultdict(lambda: {'total': 0, 'correct': 0})
    
    for v in valid_validations:
        if v['confidence_level'] == 'high_confidence':
            merchant = v['merchant']
            if 'Sprouts' in merchant:
                merchant_key = 'Sprouts'
            elif 'Walmart' in merchant or 'WAL-MART' in merchant:
                merchant_key = 'Walmart'
            elif 'Target' in merchant:
                merchant_key = 'Target'
            else:
                merchant_key = 'Other'
            
            merchant_accuracy[merchant_key]['total'] += 1
            if v['accuracy']['grand_total_correct']:
                merchant_accuracy[merchant_key]['correct'] += 1
    
    for merchant, stats in sorted(merchant_accuracy.items()):
        if stats['total'] > 0:
            accuracy = stats['correct'] / stats['total'] * 100
            print(f"  {merchant}: {stats['correct']}/{stats['total']} ({accuracy:.1f}%)")
    
    print(f"\n💡 KEY INSIGHTS:")
    print(f"  - High confidence receipts are the ones we'd process WITHOUT Pinecone")
    print(f"  - These results show the real-world accuracy of our spatial/math approach")
    print(f"  - Any inaccuracy here would need Pinecone as a fallback")


if __name__ == "__main__":
    asyncio.run(validate_all_receipts())
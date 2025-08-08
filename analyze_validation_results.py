#!/usr/bin/env python3
"""
Comprehensive analysis of receipt validation results.
Generates detailed statistics and HTML report from validation data.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple


def load_validation_data(export_dir: Path) -> List[Dict[str, Any]]:
    """Load all validation results from exported JSON files."""
    results = []
    
    for json_file in export_dir.glob("*.json"):
        with open(json_file) as f:
            data = json.load(f)
            
            # Extract relevant data
            result = {
                "file": json_file.name,
                "image_id": json_file.stem,
                "receipts": data.get("receipts", []),
                "receipt_count": len(data.get("receipts", [])),
                "has_metadata": "selected_metadata" in data,
                "selected_metadata": data.get("selected_metadata"),
                "validation_runs": data.get("validation_runs", []),
                "receipt_words": data.get("receipt_words", []),
                "receipt_lines": data.get("receipt_lines", [])
            }
            
            # Add extraction statistics
            words_with_extracted = sum(
                1 for w in data.get("receipt_words", [])
                if w.get("extracted_data") is not None
            )
            result["words_with_extracted_data"] = words_with_extracted
            result["total_words"] = len(data.get("receipt_words", []))
            
            results.append(result)
    
    return results


def analyze_validation_methods(results: List[Dict]) -> Dict[str, Any]:
    """Analyze validation method effectiveness."""
    method_stats = defaultdict(lambda: {"count": 0, "with_place_id": 0, "merchants": []})
    
    for result in results:
        if result["has_metadata"]:
            meta = result["selected_metadata"]
            method = meta.get("validated_by", "UNKNOWN")
            
            method_stats[method]["count"] += 1
            
            place_id = meta.get("place_id", "")
            if place_id and len(place_id) > 10 and place_id != "unknown":
                method_stats[method]["with_place_id"] += 1
            
            merchant = meta.get("merchant_name", "")
            if merchant:
                method_stats[method]["merchants"].append(merchant)
    
    # Calculate success rates
    for method, stats in method_stats.items():
        stats["success_rate"] = (
            stats["with_place_id"] / stats["count"] * 100
            if stats["count"] > 0 else 0
        )
        stats["unique_merchants"] = len(set(stats["merchants"]))
    
    return dict(method_stats)


def analyze_extraction_quality(results: List[Dict]) -> Dict[str, Any]:
    """Analyze the quality of extracted data from Apple's NL API."""
    extraction_stats = {
        "total_words": 0,
        "words_with_extraction": 0,
        "extraction_types": Counter(),
        "receipts_with_extraction": 0,
        "avg_extraction_per_receipt": 0
    }
    
    receipts_with_any_extraction = 0
    
    for result in results:
        total_words = result["total_words"]
        words_with_extracted = result["words_with_extracted_data"]
        
        extraction_stats["total_words"] += total_words
        extraction_stats["words_with_extraction"] += words_with_extracted
        
        if words_with_extracted > 0:
            receipts_with_any_extraction += 1
        
        # Count extraction types
        for word in result["receipt_words"]:
            if word.get("extracted_data"):
                ext_type = word["extracted_data"].get("type", "unknown")
                extraction_stats["extraction_types"][ext_type] += 1
    
    extraction_stats["receipts_with_extraction"] = receipts_with_any_extraction
    extraction_stats["avg_extraction_per_receipt"] = (
        extraction_stats["words_with_extraction"] / len(results)
        if results else 0
    )
    
    return extraction_stats


def analyze_failure_patterns(results: List[Dict]) -> Dict[str, List]:
    """Identify common patterns in failed validations."""
    failures = {
        "no_place_id": [],
        "inference_only": [],
        "no_metadata": [],
        "empty_merchant": []
    }
    
    for result in results:
        if not result["has_metadata"]:
            failures["no_metadata"].append({
                "file": result["file"],
                "receipt_count": result["receipt_count"]
            })
            continue
        
        meta = result["selected_metadata"]
        
        # Check for various failure modes
        place_id = meta.get("place_id", "")
        if not place_id or len(place_id) <= 10 or place_id == "unknown":
            failures["no_place_id"].append({
                "file": result["file"],
                "merchant": meta.get("merchant_name", "UNKNOWN"),
                "method": meta.get("validated_by", "UNKNOWN")
            })
        
        if meta.get("validated_by") == "INFERENCE":
            failures["inference_only"].append({
                "file": result["file"],
                "merchant": meta.get("merchant_name", "UNKNOWN"),
                "reasoning": meta.get("reasoning", "")
            })
        
        if not meta.get("merchant_name") or meta.get("merchant_name") == "UNKNOWN":
            failures["empty_merchant"].append({
                "file": result["file"],
                "method": meta.get("validated_by", "UNKNOWN")
            })
    
    return failures


def analyze_multi_run_effectiveness(results: List[Dict]) -> Dict[str, Any]:
    """Analyze how multiple validation runs improve results."""
    multi_run_stats = {
        "receipts_with_multiple_runs": 0,
        "avg_runs_per_receipt": 0,
        "consistency_scores": [],
        "method_variations": []
    }
    
    for result in results:
        runs = result.get("validation_runs", [])
        if len(runs) > 1:
            multi_run_stats["receipts_with_multiple_runs"] += 1
            
            # Check consistency across runs
            merchants = [r["metadata"]["merchant_name"] for r in runs if r.get("metadata")]
            methods = [r["metadata"]["validated_by"] for r in runs if r.get("metadata")]
            
            if merchants:
                # Calculate consistency score (how often same merchant found)
                most_common = Counter(merchants).most_common(1)[0][1]
                consistency = most_common / len(merchants)
                multi_run_stats["consistency_scores"].append(consistency)
            
            if len(set(methods)) > 1:
                multi_run_stats["method_variations"].append({
                    "file": result["file"],
                    "methods": list(set(methods))
                })
    
    if multi_run_stats["consistency_scores"]:
        multi_run_stats["avg_consistency"] = (
            sum(multi_run_stats["consistency_scores"]) / 
            len(multi_run_stats["consistency_scores"])
        )
    
    multi_run_stats["avg_runs_per_receipt"] = (
        sum(len(r.get("validation_runs", [])) for r in results) / len(results)
        if results else 0
    )
    
    return multi_run_stats


def generate_html_report(
    results: List[Dict],
    method_stats: Dict,
    extraction_stats: Dict,
    failures: Dict,
    multi_run_stats: Dict
) -> str:
    """Generate comprehensive HTML report."""
    
    total_receipts = sum(r["receipt_count"] for r in results)
    with_metadata = sum(1 for r in results if r["has_metadata"])
    with_place_id = sum(
        1 for r in results 
        if r["has_metadata"] and 
        r["selected_metadata"].get("place_id") and 
        len(str(r["selected_metadata"].get("place_id", ""))) > 10
    )
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Receipt Validation Analysis Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: #f9f9f9; padding: 15px; border-radius: 8px; border-left: 4px solid #4CAF50; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #4CAF50; }}
        .stat-label {{ color: #666; margin-top: 5px; font-size: 14px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th {{ background: #4CAF50; color: white; padding: 12px; text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
        tr:hover {{ background: #f5f5f5; }}
        .success {{ color: #4CAF50; font-weight: bold; }}
        .warning {{ color: #ff9800; font-weight: bold; }}
        .error {{ color: #f44336; font-weight: bold; }}
        .progress-bar {{ background: #e0e0e0; border-radius: 10px; overflow: hidden; height: 20px; margin: 5px 0; }}
        .progress-fill {{ background: #4CAF50; height: 100%; transition: width 0.3s; }}
        .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Receipt Validation Analysis Report</h1>
        <p class="timestamp">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        
        <h2>Overall Statistics</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{total_receipts}</div>
                <div class="stat-label">Total Receipts</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{with_metadata}</div>
                <div class="stat-label">With Metadata</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{with_place_id}</div>
                <div class="stat-label">With Place ID</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{with_place_id/with_metadata*100:.1f}%</div>
                <div class="stat-label">Success Rate</div>
            </div>
        </div>
        
        <h2>Validation Methods Performance</h2>
        <table>
            <tr>
                <th>Method</th>
                <th>Count</th>
                <th>With Place ID</th>
                <th>Success Rate</th>
                <th>Unique Merchants</th>
            </tr>
"""
    
    for method, stats in sorted(method_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        success_class = "success" if stats["success_rate"] > 70 else "warning" if stats["success_rate"] > 40 else "error"
        html += f"""
            <tr>
                <td><strong>{method}</strong></td>
                <td>{stats['count']}</td>
                <td>{stats['with_place_id']}</td>
                <td class="{success_class}">{stats['success_rate']:.1f}%</td>
                <td>{stats['unique_merchants']}</td>
            </tr>
"""
    
    html += f"""
        </table>
        
        <h2>Data Extraction Quality</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{extraction_stats['words_with_extraction']:,}</div>
                <div class="stat-label">Words with Extracted Data</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{extraction_stats['total_words']:,}</div>
                <div class="stat-label">Total Words</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{extraction_stats['receipts_with_extraction']}</div>
                <div class="stat-label">Receipts with Extraction</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{extraction_stats['avg_extraction_per_receipt']:.1f}</div>
                <div class="stat-label">Avg Extractions/Receipt</div>
            </div>
        </div>
        
        <h3>Extraction Types</h3>
        <table>
            <tr><th>Type</th><th>Count</th></tr>
"""
    
    for ext_type, count in extraction_stats["extraction_types"].most_common():
        html += f"            <tr><td>{ext_type}</td><td>{count}</td></tr>\n"
    
    html += f"""
        </table>
        
        <h2>Multi-Run Analysis</h2>
        <p>Average runs per receipt: <strong>{multi_run_stats['avg_runs_per_receipt']:.1f}</strong></p>
        <p>Receipts with multiple runs: <strong>{multi_run_stats['receipts_with_multiple_runs']}</strong></p>
        <p>Average consistency score: <strong>{multi_run_stats.get('avg_consistency', 0):.2%}</strong></p>
        
        <h2>Failure Analysis</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{len(failures['no_place_id'])}</div>
                <div class="stat-label">Missing Place ID</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(failures['inference_only'])}</div>
                <div class="stat-label">Inference Only</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(failures['no_metadata'])}</div>
                <div class="stat-label">No Metadata</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(failures['empty_merchant'])}</div>
                <div class="stat-label">Unknown Merchant</div>
            </div>
        </div>
        
        <h3>Common Failure Patterns</h3>
        <ul>
"""
    
    # Add top failure examples
    for fail in failures["no_place_id"][:5]:
        html += f"            <li>{fail['merchant']} ({fail['method']}) - {fail['file']}</li>\n"
    
    html += """
        </ul>
        
        <h2>Recommendations</h2>
        <ol>
            <li><strong>Improve Phone Extraction:</strong> Many receipts have incomplete phone numbers</li>
            <li><strong>Address Normalization:</strong> Better parsing of address components</li>
            <li><strong>Merchant Name Cleanup:</strong> Remove receipt artifacts from merchant names</li>
            <li><strong>Cache Common Merchants:</strong> Build local database of frequently seen merchants</li>
            <li><strong>Confidence Scoring:</strong> Add confidence scores to choose best validation method</li>
        </ol>
    </div>
</body>
</html>
"""
    
    return html


def main():
    """Run comprehensive analysis and generate report."""
    export_dir = Path("dev.local_export")
    
    if not export_dir.exists():
        print(f"Error: {export_dir} directory not found")
        return
    
    print("Loading validation data...")
    results = load_validation_data(export_dir)
    
    print("Analyzing validation methods...")
    method_stats = analyze_validation_methods(results)
    
    print("Analyzing extraction quality...")
    extraction_stats = analyze_extraction_quality(results)
    
    print("Analyzing failure patterns...")
    failures = analyze_failure_patterns(results)
    
    print("Analyzing multi-run effectiveness...")
    multi_run_stats = analyze_multi_run_effectiveness(results)
    
    print("Generating HTML report...")
    html = generate_html_report(
        results, method_stats, extraction_stats, failures, multi_run_stats
    )
    
    # Save report
    report_path = Path("validation_analysis_report.html")
    with open(report_path, "w") as f:
        f.write(html)
    
    print(f"\n✅ Report generated: {report_path}")
    print("\nQuick Summary:")
    print(f"  Total receipts: {sum(r['receipt_count'] for r in results)}")
    print(f"  Success rate: {sum(1 for r in results if r['has_metadata'] and r['selected_metadata'].get('place_id') and len(str(r['selected_metadata'].get('place_id', ''))) > 10) / len([r for r in results if r['has_metadata']]) * 100:.1f}%")
    print(f"  Most effective method: {max(method_stats.items(), key=lambda x: x[1]['success_rate'])[0]}")
    print(f"  Receipts needing attention: {len(failures['no_place_id'])}")


if __name__ == "__main__":
    main()
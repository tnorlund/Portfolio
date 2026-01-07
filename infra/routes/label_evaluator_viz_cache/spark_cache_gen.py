"""Generate cache using PySpark for Parquet + DynamoDB lookup."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/tnorlund/portfolio_agent_evaluator_visualization/Portfolio/receipt_dynamo")

from pyspark.sql import SparkSession
from pyspark.sql.functions import col

from receipt_dynamo import DynamoClient

PARQUET_DIR = "/tmp/cache-dev/parquet"
S3_RESULTS_DIR = Path("/tmp/cache-dev/s3-results")
OUTPUT_FILE = Path("/tmp/cache-dev/viz-sample-data-spark.json")
DYNAMODB_TABLE = "ReceiptsTable-dc5be22"
MAX_RECEIPTS = 10

def load_all_receipts_from_dynamo():
    """Load all receipts into memory for fast lookup."""
    print("Loading all receipts from DynamoDB...")
    client = DynamoClient(table_name=DYNAMODB_TABLE)
    receipts, _ = client.list_receipts(limit=None)
    
    lookup = {}
    for r in receipts:
        key = (r.image_id, r.receipt_id)
        lookup[key] = {
            'cdn_s3_key': r.cdn_s3_key or '',
            'cdn_webp_s3_key': r.cdn_webp_s3_key,
            'cdn_medium_s3_key': r.cdn_medium_s3_key,
            'width': r.width,
            'height': r.height,
        }
    
    print(f"  Loaded {len(lookup)} receipts into memory")
    return lookup

def load_s3_result(result_type: str, image_id: str, receipt_id: int):
    """Load result from local S3 copy."""
    path = S3_RESULTS_DIR / result_type / f"{image_id}_{receipt_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None

def parse_label_string(label_str: str) -> dict:
    """Parse stringified ReceiptWordLabel to dict."""
    result = {}
    patterns = {
        'line_id': r"line_id=(\d+)",
        'word_id': r"word_id=(\d+)",
        'label': r"label='([^']+)'",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, label_str)
        if match:
            val = match.group(1)
            result[field] = int(val) if field in ('line_id', 'word_id') else val
    return result

def main():
    receipt_lookup = load_all_receipts_from_dynamo()
    
    print("\nInitializing Spark...")
    spark = SparkSession.builder \
        .appName("CacheGenerator") \
        .master("local[*]") \
        .config("spark.driver.memory", "4g") \
        .config("spark.sql.legacy.parquet.nanosAsLong", "true") \
        .getOrCreate()
    
    spark.sparkContext.setLogLevel("WARN")
    
    print(f"\nReading Parquet files from {PARQUET_DIR}...")
    df = spark.read.parquet(PARQUET_DIR)
    total_count = df.count()
    print(f"  Total traces: {total_count}")
    
    langgraph_df = df.filter(col("name") == "LangGraph")
    langgraph_count = langgraph_df.count()
    print(f"  LangGraph traces: {langgraph_count}")
    
    print("\nExtracting receipt data...")
    langgraph_data = langgraph_df.select("outputs").collect()
    
    receipts_from_parquet = []
    for row in langgraph_data:
        outputs = json.loads(row.outputs) if isinstance(row.outputs, str) else row.outputs
        if not outputs:
            continue
            
        image_id = outputs.get('image_id')
        receipt_id = outputs.get('receipt_id')
        if not image_id or receipt_id is None:
            continue
        
        words = outputs.get('words', [])
        labels_raw = outputs.get('labels', [])
        labels_lookup = {}
        for label_str in labels_raw:
            if isinstance(label_str, str):
                parsed = parse_label_string(label_str)
                if 'line_id' in parsed and 'word_id' in parsed:
                    labels_lookup[(parsed['line_id'], parsed['word_id'])] = parsed.get('label')
        
        receipts_from_parquet.append({
            'image_id': image_id,
            'receipt_id': receipt_id,
            'words': words,
            'labels_lookup': labels_lookup,
        })
    
    print(f"  Extracted {len(receipts_from_parquet)} receipts from Parquet")
    
    print("\nBuilding visualization data...")
    viz_receipts = []
    
    for parquet_data in receipts_from_parquet:
        image_id = parquet_data['image_id']
        receipt_id = parquet_data['receipt_id']
        
        dynamo_data = receipt_lookup.get((image_id, receipt_id))
        if not dynamo_data:
            continue
        
        currency = load_s3_result('currency', image_id, receipt_id)
        metadata = load_s3_result('metadata', image_id, receipt_id)
        financial = load_s3_result('financial', image_id, receipt_id)
        geometric = load_s3_result('results', image_id, receipt_id)
        
        if not currency:
            continue
        
        merchant_name = currency.get('merchant_name', 'Unknown')
        
        words = []
        for w in parquet_data['words']:
            line_id = w.get('line_id')
            word_id = w.get('word_id')
            label = parquet_data['labels_lookup'].get((line_id, word_id))
            bbox = w.get('bounding_box', {})
            words.append({
                'text': w.get('text', ''),
                'label': label,
                'line_id': line_id,
                'word_id': word_id,
                'bbox': {
                    'x': bbox.get('x', 0),
                    'y': bbox.get('y', 0),
                    'width': bbox.get('width', 0),
                    'height': bbox.get('height', 0),
                },
            })
        
        issues_found = (
            (geometric or {}).get('issues_found', 0)
            + (currency or {}).get('decisions', {}).get('INVALID', 0)
            + (currency or {}).get('decisions', {}).get('NEEDS_REVIEW', 0)
            + (metadata or {}).get('decisions', {}).get('INVALID', 0)
            + (metadata or {}).get('decisions', {}).get('NEEDS_REVIEW', 0)
            + (financial or {}).get('decisions', {}).get('INVALID', 0)
            + (financial or {}).get('decisions', {}).get('NEEDS_REVIEW', 0)
        )
        
        viz_receipts.append({
            'image_id': image_id,
            'receipt_id': receipt_id,
            'merchant_name': merchant_name,
            'issues_found': issues_found,
            'words': words,
            'geometric': {
                'issues_found': (geometric or {}).get('issues_found', 0),
                'issues': (geometric or {}).get('issues', []),
                'duration_seconds': (geometric or {}).get('duration_seconds', 0),
            },
            'currency': {
                'decisions': (currency or {}).get('decisions', {}),
                'all_decisions': (currency or {}).get('all_decisions', []),
                'duration_seconds': (currency or {}).get('duration_seconds', 0),
            },
            'metadata': {
                'decisions': (metadata or {}).get('decisions', {}),
                'all_decisions': (metadata or {}).get('all_decisions', []),
                'duration_seconds': (metadata or {}).get('duration_seconds', 0),
            },
            'financial': {
                'decisions': (financial or {}).get('decisions', {}),
                'all_decisions': (financial or {}).get('all_decisions', []),
                'duration_seconds': (financial or {}).get('duration_seconds', 0),
            },
            'cdn_s3_key': dynamo_data['cdn_s3_key'],
            'cdn_webp_s3_key': dynamo_data.get('cdn_webp_s3_key'),
            'cdn_medium_s3_key': dynamo_data.get('cdn_medium_s3_key'),
            'width': dynamo_data['width'],
            'height': dynamo_data['height'],
        })
    
    viz_receipts.sort(key=lambda r: -r['issues_found'])
    selected = viz_receipts[:MAX_RECEIPTS]
    
    print(f"\nBuilt {len(viz_receipts)} total, selected top {len(selected)}")
    
    cache = {
        'execution_id': 'spark-generated',
        'receipts': selected,
        'summary': {
            'total_receipts': len(selected),
            'receipts_with_issues': len([r for r in selected if r['issues_found'] > 0]),
        },
        'cached_at': datetime.now(timezone.utc).isoformat(),
    }
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(cache, f, indent=2, default=str)
    
    print(f"\nCache written to {OUTPUT_FILE}")
    for r in selected[:5]:
        print(f"  {r['merchant_name']}: {r['issues_found']} issues, {len(r['words'])} words")
    
    spark.stop()

if __name__ == "__main__":
    main()

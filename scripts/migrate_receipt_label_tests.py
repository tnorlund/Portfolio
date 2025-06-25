#!/usr/bin/env python3
"""
Script to migrate receipt_label tests from marker-based to directory-based organization.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List


def extract_markers_from_file(file_path: Path) -> List[str]:
    """Extract pytest markers from a test file."""
    markers = []
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Find all @pytest.mark.* decorators
        marker_pattern = r'@pytest\.mark\.(\w+)'
        found_markers = re.findall(marker_pattern, content)
        
        # Filter to our known test type markers
        test_type_markers = ['unit', 'integration', 'end_to_end']
        markers = [m for m in found_markers if m in test_type_markers]
        
    except Exception as e:
        print(f"Warning: Could not parse {file_path}: {e}")
    
    return markers


def analyze_receipt_label_tests():
    """Analyze current receipt_label test organization."""
    test_dir = Path("receipt_label/receipt_label/tests")
    if not test_dir.exists():
        print("receipt_label tests directory not found")
        return
    
    file_analysis = {}
    
    # Find all test files
    test_files = list(test_dir.rglob("test_*.py"))
    
    for test_file in test_files:
        markers = extract_markers_from_file(test_file)
        relative_path = test_file.relative_to(test_dir)
        
        file_analysis[str(relative_path)] = {
            'markers': markers,
            'primary_marker': markers[0] if markers else 'unit',  # Default to unit
            'mixed_markers': len(set(markers)) > 1
        }
    
    return file_analysis


def create_migration_plan(analysis: Dict) -> Dict:
    """Create a plan for migrating files to directory structure."""
    plan = {
        'unit': [],
        'integration': [], 
        'end_to_end': [],
        'mixed': []
    }
    
    for file_path, info in analysis.items():
        if info['mixed_markers']:
            plan['mixed'].append({
                'file': file_path,
                'markers': info['markers'],
                'action': 'needs_manual_review'
            })
        else:
            primary_marker = info['primary_marker']
            plan[primary_marker].append({
                'file': file_path,
                'action': 'move_to_directory'
            })
    
    return plan


def print_migration_analysis():
    """Print analysis of current receipt_label test structure."""
    print("🔍 Receipt Label Test Migration Analysis")
    print("=" * 50)
    
    analysis = analyze_receipt_label_tests()
    if not analysis:
        return
    
    plan = create_migration_plan(analysis)
    
    print(f"\n📊 Current Test Distribution:")
    for test_type, files in plan.items():
        if test_type != 'mixed':
            print(f"   {test_type.upper()}: {len(files)} files")
    
    if plan['mixed']:
        print(f"   MIXED MARKERS: {len(plan['mixed'])} files (need manual review)")
    
    print(f"\n📁 Proposed Directory Structure:")
    print("receipt_label/tests/")
    print("├── unit/")
    for file_info in plan['unit']:
        print(f"│   └── {file_info['file']}")
    
    print("├── integration/") 
    for file_info in plan['integration']:
        print(f"│   └── {file_info['file']}")
    
    if plan['end_to_end']:
        print("└── end_to_end/")
        for file_info in plan['end_to_end']:
            print(f"    └── {file_info['file']}")
    
    if plan['mixed']:
        print(f"\n⚠️  Files with Mixed Markers (require manual review):")
        for file_info in plan['mixed']:
            print(f"   • {file_info['file']}: {file_info['markers']}")
            print(f"     → Suggest: Split into separate files or choose primary marker")
    
    print(f"\n🚀 Migration Commands:")
    print("# Create new directory structure")
    print("mkdir -p receipt_label/tests/{unit,integration,end_to_end}")
    print()
    
    # Generate move commands
    for test_type in ['unit', 'integration', 'end_to_end']:
        if plan[test_type]:
            print(f"# Move {test_type} tests")
            for file_info in plan[test_type]:
                src = f"receipt_label/receipt_label/tests/{file_info['file']}"
                dst = f"receipt_label/tests/{test_type}/{Path(file_info['file']).name}"
                print(f"mv '{src}' '{dst}'")
            print()
    
    print("# Update workflow to use correct paths")
    print("# receipt_label/tests/unit instead of tests/unit")
    print("# receipt_label/tests/integration instead of tests/integration")


def execute_migration(dry_run=True):
    """Execute the migration (with dry-run by default)."""
    analysis = analyze_receipt_label_tests()
    if not analysis:
        print("No tests found to migrate")
        return
    
    plan = create_migration_plan(analysis)
    
    # Create directory structure
    base_dir = Path("receipt_label/tests")
    
    if not dry_run:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / "unit").mkdir(exist_ok=True)
        (base_dir / "integration").mkdir(exist_ok=True)
        (base_dir / "end_to_end").mkdir(exist_ok=True)
    
    print(f"{'[DRY RUN]' if dry_run else '[EXECUTING]'} Migration Plan:")
    
    # Move files
    for test_type in ['unit', 'integration', 'end_to_end']:
        if plan[test_type]:
            print(f"\n📁 {test_type.upper()} tests:")
            for file_info in plan[test_type]:
                src = Path(f"receipt_label/receipt_label/tests/{file_info['file']}")
                dst = base_dir / test_type / src.name
                
                print(f"   {src} → {dst}")
                
                if not dry_run and src.exists():
                    shutil.move(str(src), str(dst))
    
    # Report mixed files
    if plan['mixed']:
        print(f"\n⚠️  Files requiring manual review:")
        for file_info in plan['mixed']:
            print(f"   • {file_info['file']}: {file_info['markers']}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate receipt_label tests to directory structure")
    parser.add_argument("--analyze", action="store_true", help="Analyze current structure")
    parser.add_argument("--execute", action="store_true", help="Execute migration")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Show what would be done")
    
    args = parser.parse_args()
    
    if args.analyze or (not args.execute):
        print_migration_analysis()
    
    if args.execute:
        execute_migration(dry_run=args.dry_run)
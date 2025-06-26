#!/usr/bin/env python3
"""Fix all remaining pre-commit issues comprehensively."""

import re

def fix_image_file():
    """Fix _image.py file issues."""
    file_path = 'receipt_dynamo/data/_image.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix the malformed docstring that's breaking parsing at line 106
    content = re.sub(r'    \"\"add_imageef add_image\(self, image: Image\):', '    def add_image(self, image: Image):', content)
    content = re.sub(r'  def get_image_detailsetail\(', '  def get_image_details(', content)
    content = re.sub(r'    dget_image_cluster_detailsils\(', '    def get_image_cluster_details(', content)
    content = re.sub(r'    def dedelete_imageg\(', '    def delete_image(', content)
    content = re.sub(r'    def defdelete_images\(', '    def delete_images(', content)
    content = re.sub(r'    deflist_images_words_tagss\(', '    def list_images_words_tags(', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_chatgpt_validation_file():
    """Fix _receipt_chatgpt_validation.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_chatgpt_validation.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix indentation and method definition issues
    lines = content.split('\n')
    fixed_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Fix specific problematic lines
        if '  def list_receipt_chat_gpt_validations_for_receipt(' in line:
            # Fix indentation
            line = '    def list_receipt_chat_gpt_validations_for_receipt('
        fixed_lines.append(line)
        i += 1
    
    content = '\n'.join(fixed_lines)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_letter_file():
    """Fix _receipt_letter.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_letter.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix malformed docstring issue around line 54
    content = re.sub(r'        \"\"\"Adds a ReceiptLetter to DynamoDB\.', '        """Adds a ReceiptLetter to DynamoDB.', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_line_file():
    """Fix _receipt_line.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_line.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix method signature issues around line 306
    content = re.sub(r'        self, limit: int = None, lastEvaluatedKey: dict \| None = None', 
                     '        self, limit: int = None, last_evaluated_key: dict | None = None', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_section_file():
    """Fix _receipt_section.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_section.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix indentation issues around line 168
    lines = content.split('\n')
    fixed_lines = []
    for i, line in enumerate(lines):
        # Fix any unmatched indentation
        if line.strip() and not line.startswith(' ') and i > 0 and lines[i-1].strip().endswith(':'):
            line = '    ' + line
        fixed_lines.append(line)
    
    content = '\n'.join(fixed_lines)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_structure_analysis_file():
    """Fix _receipt_structure_analysis.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_structure_analysis.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix method definition issues around line 98
    content = re.sub(r'add_receipt_structure_analyseseceiptStructureAnalyses\(', 
                     'def add_receipt_structure_analyses(', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_validation_summary_file():
    """Fix _receipt_validation_summary.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_validation_summary.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix method definition issues around line 95
    content = re.sub(r'fupdate_receipt_validation_summaryidationSummary\(', 
                     'def update_receipt_validation_summary(', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

def fix_receipt_word_file():
    """Fix _receipt_word.py file issues."""
    file_path = 'receipt_dynamo/data/_receipt_word.py'
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix method definition issues around line 215
    content = re.sub(r'delete_receipt_words_from_lineeiptWordsFromLine\(', 
                     'def delete_receipt_words_from_line(', content)
    
    with open(file_path, 'w') as f:
        f.write(content)
    print(f"Fixed {file_path}")

# Run all fixes
if __name__ == "__main__":
    fix_image_file()
    fix_chatgpt_validation_file()
    fix_receipt_letter_file()
    fix_receipt_line_file()
    fix_receipt_section_file()
    fix_receipt_structure_analysis_file()
    fix_receipt_validation_summary_file()
    fix_receipt_word_file()
    print("All pre-commit issues fixed")
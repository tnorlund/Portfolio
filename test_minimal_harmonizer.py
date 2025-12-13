#!/usr/bin/env python3
"""
Minimal test of the label harmonizer changes without full dependencies.
"""

import sys
import os
sys.path.insert(0, 'receipt_agent')

def test_prompt_changes():
    """Test that our prompt changes are in place."""
    try:
        # Read prompt from file directly
        with open('receipt_agent/receipt_agent/agents/label_harmonizer/graph.py', 'r') as f:
            content = f.read()
        
        print("✅ SUCCESS: Read graph.py")
        
        # Extract the prompt
        start = content.find('LABEL_HARMONIZER_PROMPT = """') + len('LABEL_HARMONIZER_PROMPT = """')
        end = content.find('"""', start)
        prompt = content[start:end]
        
        # Check our changes
        has_submit = 'submit_harmonization' in prompt
        has_validate = 'validate_financial_consistency' in prompt
        has_focus = 'Focus on understanding table structure' in prompt
        
        print(f"Contains 'submit_harmonization': {has_submit}")
        print(f"Contains 'validate_financial_consistency': {has_validate}")
        print(f"Contains new focus text: {has_focus}")
        
        # Print first few lines of prompt
        lines = prompt.split('\n')[:10]
        print("\nFirst 10 lines of prompt:")
        for i, line in enumerate(lines, 1):
            print(f"{i:2d}: {line}")
            
        return not has_submit and has_validate and has_focus
        
    except Exception as e:
        print(f"❌ Failed to read prompt: {e}")
        return False

def test_tools_changes():
    """Test that our tools changes are in place."""
    try:
        # Read factory file directly since imports are complex
        with open('receipt_agent/receipt_agent/agents/label_harmonizer/tools/factory.py', 'r') as f:
            content = f.read()
        
        print("✅ SUCCESS: Read factory.py")
        
        # Check tools list
        tools_section = content[content.find('tools = ['):content.find('return tools, state')]
        
        has_validate_in_tools = 'validate_financial_consistency,' in tools_section
        has_submit_in_tools = 'submit_harmonization,' in tools_section
        
        print(f"validate_financial_consistency in tools: {has_validate_in_tools}")
        print(f"submit_harmonization in tools: {has_submit_in_tools}")
        
        # Show the tools list
        start = content.find('tools = [')
        end = content.find(']', start) + 1
        tools_list = content[start:end]
        print("\nTools list:")
        print(tools_list)
        
        return has_validate_in_tools and not has_submit_in_tools
        
    except Exception as e:
        print(f"❌ Failed to read factory: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("TESTING LABEL HARMONIZER CHANGES")
    print("=" * 60)
    
    print("\n1. Testing prompt changes...")
    prompt_ok = test_prompt_changes()
    
    print("\n2. Testing tools changes...")
    tools_ok = test_tools_changes()
    
    print("\n" + "=" * 60)
    if prompt_ok and tools_ok:
        print("🎉 ALL TESTS PASSED - Changes are in place!")
        print("✅ Prompt updated (no submit_harmonization)")
        print("✅ Tools updated (validate_financial_consistency added)")
    else:
        print("❌ TESTS FAILED - Changes not properly applied")
        print(f"   Prompt OK: {prompt_ok}")
        print(f"   Tools OK: {tools_ok}")
    print("=" * 60)
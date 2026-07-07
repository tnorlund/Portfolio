#!/usr/bin/env python3
"""Run the receipt-logo MCP server from a Portfolio source checkout."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "receipt_logo"))

from receipt_logo.mcp_server import main

if __name__ == "__main__":
    main()

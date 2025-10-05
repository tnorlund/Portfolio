#!/usr/bin/env python3
"""
Python wrapper for the Swift OCR CLI tool.

This script replaces the original Python OCR implementation with a call to the Swift binary.
It provides the same interface and error handling as the original Python script.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def find_swift_binary():
    """Find the Swift OCR binary, checking common locations."""
    # Check if we're in a Swift package directory
    swift_package_dir = (
        Path(__file__).parent.parent.parent / "receipt_ocr_swift"
    )
    if swift_package_dir.exists():
        # Try to find the built binary
        build_dir = swift_package_dir / ".build" / "debug"
        binary_path = build_dir / "receipt-ocr"
        if binary_path.exists():
            return str(binary_path)

        # Try release build
        build_dir = swift_package_dir / ".build" / "release"
        binary_path = build_dir / "receipt-ocr"
        if binary_path.exists():
            return str(binary_path)

    # Check if it's in PATH
    try:
        result = subprocess.run(
            ["which", "receipt-ocr"], capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass

    return None


def build_swift_binary():
    """Build the Swift OCR binary."""
    swift_package_dir = (
        Path(__file__).parent.parent.parent / "receipt_ocr_swift"
    )
    if not swift_package_dir.exists():
        raise RuntimeError(
            f"Swift package directory not found: {swift_package_dir}"
        )

    print("Building Swift OCR binary...")
    try:
        result = subprocess.run(
            ["swift", "build", "--product", "receipt-ocr"],
            cwd=swift_package_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        print("Swift binary built successfully!")

        # Return the path to the built binary
        build_dir = swift_package_dir / ".build" / "debug"
        binary_path = build_dir / "receipt-ocr"
        if binary_path.exists():
            return str(binary_path)
        else:
            raise RuntimeError(
                "Binary was built but not found at expected location"
            )

    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to build Swift binary:\n"
        error_msg += f"Return code: {e.returncode}\n"
        if e.stdout:
            error_msg += f"STDOUT:\n{e.stdout}\n"
        if e.stderr:
            error_msg += f"STDERR:\n{e.stderr}\n"
        raise RuntimeError(error_msg)
    except FileNotFoundError:
        raise RuntimeError(
            "Swift compiler not found. Please install Swift from https://swift.org/"
        )


def main():
    parser = argparse.ArgumentParser(
        description="OCR processing using Swift implementation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script is a Python wrapper for the Swift OCR implementation.

To build the Swift binary:
  cd receipt_ocr_swift
  swift build --product receipt-ocr

The Swift binary will be available at:
  receipt_ocr_swift/.build/debug/receipt-ocr

For more information, see receipt_ocr_swift/README.md
        """,
    )
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        help="Environment name (default: dev)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["trace", "debug", "info", "warn", "error"],
        help="Log level for Swift binary",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously until queue is empty",
    )
    parser.add_argument(
        "--stub-ocr",
        action="store_true",
        help="Use stub OCR engine instead of Vision",
    )

    args = parser.parse_args()

    # Find or build the Swift binary
    binary_path = find_swift_binary()
    if binary_path is None:
        print("Swift OCR binary not found. Attempting to build...")
        try:
            binary_path = build_swift_binary()
        except RuntimeError as e:
            print(f"Error: {e}")
            print("\nTo build the Swift binary manually:")
            print("  cd receipt_ocr_swift")
            print("  swift build --product receipt-ocr")
            print("\nFor more information, see receipt_ocr_swift/README.md")
            sys.exit(1)

    # Prepare arguments for the Swift binary
    swift_args = ["--env", args.env]

    if args.log_level:
        swift_args.extend(["--log-level", args.log_level])

    if args.continuous:
        swift_args.append("--continuous")

    if args.stub_ocr:
        swift_args.append("--stub-ocr")

    # Run the Swift binary
    try:
        print(f"Running Swift OCR binary: {binary_path}")
        result = subprocess.run([binary_path] + swift_args, check=True)
        sys.exit(result.returncode)
    except subprocess.CalledProcessError as e:
        print(f"Swift OCR binary failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print(f"Error: Swift binary not found at {binary_path}")
        print("Please build the Swift binary first:")
        print("  cd receipt_ocr_swift")
        print("  swift build --product receipt-ocr")
        sys.exit(1)


if __name__ == "__main__":
    main()

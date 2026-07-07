from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_logo.proof import DEFAULT_SPROUTS_SOURCE, build_sprouts_proof
from receipt_logo.receipt_fixture import inspect_receipt_fixture
from receipt_logo.vectorize import VectorizeOptions, vectorize_logo, write_vector_asset


def main() -> None:
    parser = argparse.ArgumentParser(description="Merchant logo path tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    vectorize_parser = subparsers.add_parser("vectorize")
    vectorize_parser.add_argument("source")
    vectorize_parser.add_argument("--slug", required=True)
    vectorize_parser.add_argument("--output-dir", default="proofs/vectorized")
    vectorize_parser.add_argument("--max-colors", type=int, default=4)
    vectorize_parser.add_argument("--simplify", type=float, default=1.25)

    proof_parser = subparsers.add_parser("prove-sprouts")
    proof_parser.add_argument(
        "--source",
        default=str(DEFAULT_SPROUTS_SOURCE),
    )
    proof_parser.add_argument("--output-dir", default="proofs/sprouts")
    proof_parser.add_argument(
        "--receipt-fixture",
        default=None,
    )

    inspect_parser = subparsers.add_parser("inspect-fixture")
    inspect_parser.add_argument("fixture")
    inspect_parser.add_argument("--merchant", default="Sprouts Farmers Market")

    args = parser.parse_args()

    if args.command == "vectorize":
        result = vectorize_logo(
            args.source,
            VectorizeOptions(
                max_colors=args.max_colors,
                simplify_tolerance=args.simplify,
                title=args.slug.replace("_", " "),
            ),
        )
        svg_path, manifest_path = write_vector_asset(result, args.output_dir, args.slug)
        print(
            json.dumps(
                {
                    "svg_path": str(svg_path),
                    "manifest_path": str(manifest_path),
                    "palette": list(result.palette),
                    "path_count": sum(layer.path_count for layer in result.layers),
                    "point_count": sum(layer.point_count for layer in result.layers),
                },
                indent=2,
            )
        )
        return

    if args.command == "prove-sprouts":
        proof = build_sprouts_proof(
            source_path=args.source,
            output_dir=args.output_dir,
            receipt_fixture_path=args.receipt_fixture,
        )
        print(json.dumps(proof.__dict__, indent=2))
        return

    if args.command == "inspect-fixture":
        match = inspect_receipt_fixture(Path(args.fixture), args.merchant)
        print(json.dumps(match.to_dict() if match else None, indent=2))
        return

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    main()

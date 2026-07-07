from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from receipt_logo.proof import DEFAULT_SPROUTS_SOURCE, build_sprouts_proof
from receipt_logo.receipt_fixture import inspect_receipt_fixture
from receipt_logo.vectorize import VectorizeOptions, vectorize_logo, write_vector_asset

server = Server("logo-to-path")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="vectorize_logo",
            description=(
                "Convert a transparent merchant logo image into SVG path layers. "
                "Writes the SVG and manifest to the requested output directory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "slug": {"type": "string"},
                    "output_dir": {
                        "type": "string",
                        "default": "proofs/vectorized",
                    },
                    "max_colors": {"type": "integer", "default": 4},
                    "simplify_tolerance": {"type": "number", "default": 1.25},
                },
                "required": ["source_path", "slug"],
            },
        ),
        Tool(
            name="prove_sprouts_logo",
            description=(
                "Build the Sprouts source-of-truth proof from the downloaded PNG "
                "and a read-only Portfolio receipt fixture."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "default": "~/Downloads/Sprouts_Farmers_Market_Logo.png",
                    },
                    "output_dir": {
                        "type": "string",
                        "default": "proofs/sprouts",
                    },
                    "receipt_fixture_path": {
                        "type": "string",
                        "description": (
                            "Optional read-only Portfolio-style receipt JSON "
                            "fixture for header placement proof."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="inspect_receipt_fixture",
            description=(
                "Read a Portfolio-style receipt JSON fixture and locate the "
                "merchant-name header bounds. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "fixture_path": {"type": "string"},
                    "merchant_name": {
                        "type": "string",
                        "default": "Sprouts Farmers Market",
                    },
                },
                "required": ["fixture_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "vectorize_logo":
        result = vectorize_logo(
            arguments["source_path"],
            VectorizeOptions(
                max_colors=int(arguments.get("max_colors", 4)),
                simplify_tolerance=float(arguments.get("simplify_tolerance", 1.25)),
                title=str(arguments["slug"]).replace("_", " "),
            ),
        )
        svg_path, manifest_path = write_vector_asset(
            result,
            arguments.get("output_dir", "proofs/vectorized"),
            arguments["slug"],
        )
        payload = result.manifest(str(svg_path))
        payload["manifest_path"] = str(manifest_path)
        return [_json_text(payload)]

    if name == "prove_sprouts_logo":
        proof = build_sprouts_proof(
            source_path=arguments.get(
                "source_path",
                str(DEFAULT_SPROUTS_SOURCE),
            ),
            output_dir=arguments.get("output_dir", "proofs/sprouts"),
            receipt_fixture_path=arguments.get("receipt_fixture_path"),
        )
        return [_json_text(proof.__dict__)]

    if name == "inspect_receipt_fixture":
        match = inspect_receipt_fixture(
            Path(arguments["fixture_path"]),
            arguments.get("merchant_name", "Sprouts Farmers Market"),
        )
        return [_json_text(match.to_dict() if match else None)]

    raise ValueError(f"unknown tool: {name}")


def _json_text(payload: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload, indent=2))


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run one blind typography judge over an immutable packet manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import jsonschema
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from glyphstudio.packets import canonical_json, judge_input, verify_manifest

_SCHEMA_PATH = os.path.join(
    _ROOT,
    "tools",
    "glyph-studio",
    "schemas",
    "typography_judge_verdict.schema.json",
)
_RUNNER_ABSTAIN_REASON = "runner: invalid or missing verdict after retry"


def _load_manifest(out_dir: str) -> dict:
    pointer = Path(out_dir, "MANIFEST").read_text(encoding="ascii").strip()
    if not pointer or Path(pointer).name != pointer:
        raise ValueError("MANIFEST must name a file in --out-dir")
    with open(os.path.join(out_dir, pointer), encoding="ascii") as fh:
        return json.load(fh)


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_immutable_bytes(path: str, payload: bytes) -> None:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            existing = fh.read()
        if existing != payload:
            raise RuntimeError(f"immutable judging artifact differs: {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(payload)


def _safe_crop_path(out_dir: str, relative_path: str) -> str:
    base = os.path.abspath(out_dir)
    path = os.path.abspath(os.path.join(base, relative_path))
    if os.path.commonpath((base, path)) != base:
        raise ValueError(f"crop escapes --out-dir: {relative_path}")
    return path


def build_judging_artifacts(judge_data: dict, out_dir: str) -> tuple[str, str]:
    """Build deterministic enlargements from only the blind projection."""
    packet_dir = os.path.join(
        os.path.abspath(out_dir), "judging", judge_data["packet_id"]
    )
    # Small-cap lines lose their dot structure at x4 — the Costco b1 gate
    # showed codex misreading <25px-cap dot-matrix as proportional sans.
    # x8 keeps per-dot detail visible at exactly the sizes where it
    # matters; the factor is derived from the blind context only.
    cap_px = judge_data["context"].get("cap_px")
    line_scale = 8 if cap_px is not None and cap_px < 25 else 4
    line_path = os.path.join(packet_dir, f"line_x{line_scale}.png")
    letters_path = os.path.join(packet_dir, "letters_x8.png")

    source_line = _safe_crop_path(out_dir, judge_data["line_crop"]["path"])
    with Image.open(source_line) as image:
        line = image.convert("L")
        enlarged = line.resize(
            (line.width * line_scale, line.height * line_scale),
            resample=Image.Resampling.NEAREST,
        )
        _write_immutable_bytes(line_path, _png_bytes(enlarged))

    enlarged_letters = []
    for crop in sorted(
        judge_data["letter_crops"], key=lambda item: item["seq"]
    ):
        source = _safe_crop_path(out_dir, crop["path"])
        with Image.open(source) as image:
            glyph = image.convert("L")
            enlarged_letters.append(
                glyph.resize(
                    (glyph.width * 8, glyph.height * 8),
                    resample=Image.Resampling.NEAREST,
                )
            )
    if not enlarged_letters:
        raise ValueError(f"{judge_data['packet_id']}: no letter crops")
    width = sum(image.width for image in enlarged_letters)
    width += 16 * (len(enlarged_letters) - 1)
    height = max(image.height for image in enlarged_letters)
    sheet = Image.new("L", (width, height), color=255)
    left = 0
    for image in enlarged_letters:
        sheet.paste(image, (left, height - image.height))
        left += image.width + 16
    _write_immutable_bytes(letters_path, _png_bytes(sheet))
    return line_path, letters_path


def build_prompt(
    judge: str,
    judge_data: dict,
    manifest_content_hash: str,
    line_path: str,
    letters_path: str,
) -> str:
    """Build a single blind prompt, varying only the image-delivery text."""
    if judge == "grok":
        delivery = (
            "View these two image files with your file viewing tool before "
            f"answering: {os.path.abspath(line_path)}, "
            f"{os.path.abspath(letters_path)}."
        )
    else:
        delivery = (
            "The two attached images are, in order, the full line crop and "
            "the per-letter contact sheet."
        )
    constants = {
        "schema_version": "tj-out-1",
        "packet_id": judge_data["packet_id"],
        "judge": judge,
        "manifest_content_hash": manifest_content_hash,
    }
    return "\n\n".join(
        [
            delivery,
            (
                "Task: make a blind per-line typography judgment of one "
                "receipt scan line. The images are (a) the full line crop "
                "and (b) a horizontal contact sheet of its per-letter crops "
                "in reading order."
            ),
            (
                "Look closely at per-letter DOT CONSTRUCTION in the contact "
                "sheet: receipt printers build glyphs from discrete dots on "
                "a fixed cell grid. Dotted strokes + uniform advance width "
                "mean a monospaced printer face even when the line crop "
                "looks smooth or proportional at a glance."
            ),
            "Blind judge input:\n" + canonical_json(judge_data),
            (
                "tier is DERIVED from the provided geometry: compare cap_px "
                "and stroke_med with the receipt body medians; >=1.45x cap "
                "means large and >=1.30x stroke means bold. Compute tier "
                "from those numbers. Judge family_class, monospace, italic, "
                "weight, underline, reverse_video, and slant_deg FROM THE "
                "PIXELS."
            ),
            (
                "Your reply must END with exactly one JSON object validating "
                "against tj-out-1. Fields: schema_version; packet_id; judge; "
                "manifest_content_hash; abstain (boolean); abstain_reason "
                "(string|null); typeface {family_class: "
                "mono|sans|serif|slab|script|decorative|unknown, monospace: "
                "boolean|null, italic: boolean|null, weight: "
                "normal|bold|unknown, description: string}|null; tier: "
                "normal|bold|large|unknown|null; underline: boolean|null; "
                "reverse_video: boolean|null; slant_deg: -45..45|null; "
                "confidence {typeface,tier,underline,reverse_video,slant: "
                "numbers 0..1}|null; optional notes: string. If abstain is "
                "true, all attribute fields must be null and a nonempty "
                "reason is required. If false, all attributes are required."
            ),
            (
                "Copy these exact required constants into the JSON:\n"
                + canonical_json(constants)
            ),
            "Do not put any text after the JSON object.",
        ]
    )


def _last_json_object(text: str) -> dict | None:
    """Return the outermost object ending latest in a noisy transcript."""
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, dict]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((start + length, start, value))
    if not candidates:
        return None
    latest_end = max(item[0] for item in candidates)
    # An outer object and its final nested object can end together; the outer
    # object starts first and is the verdict the judge intended to return.
    ending_latest = [item for item in candidates if item[0] == latest_end]
    return min(ending_latest, key=lambda item: item[1])[2]


def extract_verdict(judge: str, stdout: str) -> tuple[dict | None, float]:
    """Return (verdict, call_cost_usd); cost is 0.0 when not reported."""
    if judge == "codex":
        return _last_json_object(stdout), 0.0
    try:
        document = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None, 0.0
    if not isinstance(document, dict) or not isinstance(
        document.get("text"), str
    ):
        return None, 0.0
    cost = document.get("total_cost_usd")
    cost_usd = float(cost) if isinstance(cost, (int, float)) else 0.0
    return _last_json_object(document["text"]), cost_usd


def validate_verdict(
    verdict: dict | None,
    schema: dict,
    packet_id: str,
    judge: str,
    manifest_content_hash: str,
) -> str | None:
    if verdict is None:
        return "no JSON verdict object was found"
    try:
        jsonschema.validate(verdict, schema)
    except jsonschema.ValidationError as exc:
        return "schema validation failed: " + exc.message
    expected = {
        "packet_id": packet_id,
        "judge": judge,
        "manifest_content_hash": manifest_content_hash,
    }
    for key, value in expected.items():
        if verdict.get(key) != value:
            return f"identity mismatch for {key}: expected {value!r}"
    return None


def _run_subprocess(
    command: list[str], timeout: float
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _invoke_judge(
    judge: str,
    prompt: str,
    line_path: str,
    letters_path: str,
    timeout: float,
) -> tuple[dict | None, str | None, float]:
    if judge == "codex":
        command = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-i",
            line_path,
            "-i",
            letters_path,
            "--",
            prompt,
        ]
    else:
        command = ["grok", "--output-format", "json", "-p", prompt]
    try:
        completed = _run_subprocess(command, timeout)
    except subprocess.TimeoutExpired:
        return None, f"judge timed out after {timeout:g} seconds", 0.0
    except OSError as exc:
        return None, f"judge process failed: {exc}", 0.0
    verdict, cost_usd = extract_verdict(judge, completed.stdout)
    if verdict is None:
        detail = completed.stderr.strip()
        if detail:
            return (
                None,
                f"no verdict in stdout; stderr: {detail[:300]}",
                cost_usd,
            )
    return verdict, None, cost_usd


def _runner_abstain(packet_id: str, judge: str, content_hash: str) -> dict:
    return {
        "schema_version": "tj-out-1",
        "packet_id": packet_id,
        "judge": judge,
        "manifest_content_hash": content_hash,
        "abstain": True,
        "abstain_reason": _RUNNER_ABSTAIN_REASON,
        "typeface": None,
        "tier": None,
        "underline": None,
        "reverse_video": None,
        "slant_deg": None,
        "confidence": None,
    }


def judge_packet(
    packet: dict,
    out_dir: str,
    judge: str,
    content_hash: str,
    schema: dict,
    timeout: float,
    retry: int,
) -> dict:
    started = time.monotonic()
    blind_input = judge_input(packet)
    line_path, letters_path = build_judging_artifacts(blind_input, out_dir)
    prompt = build_prompt(
        judge, blind_input, content_hash, line_path, letters_path
    )
    verdict = None
    error = None
    attempts = 0
    cost_usd = 0.0
    for attempts in range(1, retry + 2):
        attempt_prompt = prompt
        if error is not None:
            attempt_prompt += (
                "\n\nThe previous reply was invalid: "
                f"{error}. Return only the corrected JSON object with no "
                "text after it."
            )
        verdict, invocation_error, attempt_cost = _invoke_judge(
            judge, attempt_prompt, line_path, letters_path, timeout
        )
        cost_usd += attempt_cost
        error = invocation_error or validate_verdict(
            verdict, schema, blind_input["packet_id"], judge, content_hash
        )
        if error is None:
            break
    runner_abstain = error is not None
    if runner_abstain:
        verdict = _runner_abstain(
            blind_input["packet_id"], judge, content_hash
        )
    return {
        "packet_id": blind_input["packet_id"],
        "verdict": verdict,
        "runner_abstain": runner_abstain,
        "attempts": attempts,
        "elapsed_s": round(time.monotonic() - started, 1),
        "cost_usd": round(cost_usd, 6),
    }


def _read_resume(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="ascii") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                packet_id = row["packet_id"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"invalid resume JSONL line {line_number}: {exc}"
                ) from exc
            rows[packet_id] = row
    return rows


def _publish_verdicts(
    rows: dict[str, dict],
    packet_ids: set[str],
    judging_dir: str,
    content_hash: str,
    judge: str,
) -> str:
    verdicts = [
        row["verdict"]
        for packet_id, row in sorted(rows.items())
        if packet_id in packet_ids and isinstance(row.get("verdict"), dict)
    ]
    path = os.path.join(
        judging_dir, f"verdicts_{judge}_{content_hash[:16]}.json"
    )
    payload = (canonical_json(verdicts) + "\n").encode("ascii")
    # A capped smoke run publishes a partial array. A later resumed full run
    # must replace that derived view while the append-only JSONL stays intact.
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def _resume_row_is_current(
    row: dict, schema: dict, packet_id: str, judge: str, content_hash: str
) -> bool:
    verdict = row.get("verdict")
    return (
        row.get("packet_id") == packet_id
        and validate_verdict(verdict, schema, packet_id, judge, content_hash)
        is None
    )


def run(args) -> dict:
    wall_started = time.monotonic()
    out_dir = os.path.abspath(args.out_dir)
    manifest = _load_manifest(out_dir)
    problems = verify_manifest(manifest, out_dir)
    if problems:
        raise ValueError(
            "manifest verification failed:\n" + "\n".join(problems)
        )

    only = set(args.only_packet_id or [])
    packets = [
        packet
        for packet in manifest["packets"]
        if not only or packet["packet_id"] in only
    ]
    if only:
        found = {packet["packet_id"] for packet in packets}
        missing = sorted(only - found)
        if missing:
            raise ValueError("unknown packet_ids: " + ", ".join(missing))
    if args.packets is not None:
        packets = packets[: args.packets]

    judging_dir = os.path.join(out_dir, "judging")
    os.makedirs(judging_dir, exist_ok=True)
    jsonl_path = os.path.join(judging_dir, f"verdicts_{args.judge}.jsonl")
    rows = _read_resume(jsonl_path)
    schema = _load_schema()
    pending = [
        packet
        for packet in packets
        if not _resume_row_is_current(
            rows.get(packet["packet_id"], {}),
            schema,
            packet["packet_id"],
            args.judge,
            manifest["content_hash"],
        )
    ]
    write_lock = threading.Lock()
    with open(jsonl_path, "a", encoding="ascii") as output:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    judge_packet,
                    packet,
                    out_dir,
                    args.judge,
                    manifest["content_hash"],
                    schema,
                    args.timeout,
                    args.retry,
                ): packet["packet_id"]
                for packet in pending
            }
            for future in as_completed(futures):
                row = future.result()
                with write_lock:
                    output.write(canonical_json(row) + "\n")
                    output.flush()
                    rows[row["packet_id"]] = row

    selected_ids = {packet["packet_id"] for packet in packets}
    selected_rows = [rows[packet["packet_id"]] for packet in packets]
    artifact_path = _publish_verdicts(
        rows,
        selected_ids,
        judging_dir,
        manifest["content_hash"],
        args.judge,
    )
    runner_abstains = sum(
        bool(row.get("runner_abstain")) for row in selected_rows
    )
    judge_abstains = sum(
        bool(row.get("verdict", {}).get("abstain"))
        and not bool(row.get("runner_abstain"))
        for row in selected_rows
    )
    elapsed_values = [
        float(row.get("elapsed_s", 0.0)) for row in selected_rows
    ]
    summary = {
        "judged": len(selected_rows),
        "runner_abstains": runner_abstains,
        "judge_abstains": judge_abstains,
        "mean_elapsed_s": (
            round(sum(elapsed_values) / len(elapsed_values), 1)
            if elapsed_values
            else 0.0
        ),
        "total_wall_s": round(time.monotonic() - wall_started, 1),
        "total_cost_usd": round(
            sum(float(row.get("cost_usd", 0.0)) for row in selected_rows), 4
        ),
        "artifact": artifact_path,
    }
    print(
        "judged={judged} runner_abstains={runner_abstains} "
        "judge_abstains={judge_abstains} mean_elapsed_s={mean_elapsed_s:.1f} "
        "total_wall_s={total_wall_s:.1f} "
        "total_cost_usd={total_cost_usd}".format(**summary)
    )
    print(f"verdicts={artifact_path}")
    return summary


def _nonnegative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--judge", choices=("codex", "grok"), required=True)
    parser.add_argument("--packets", type=_nonnegative, default=None)
    parser.add_argument("--workers", type=_positive, default=6)
    parser.add_argument("--timeout", type=_positive, default=180)
    parser.add_argument("--retry", type=_nonnegative, default=1)
    parser.add_argument("--only-packet-id", action="append", default=None)
    args = parser.parse_args(argv)
    try:
        run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

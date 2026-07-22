#!/usr/bin/env python3
"""Trajectory tooling for the nightly loop (plan humble-skipping-quilt H1).

The nightly wrapper launches ``claude -p`` with ``--output-format stream-json
--verbose`` and captures stdout to ``$RUN_DIR/trajectory.jsonl`` — one JSON
object per line. This module turns that trace into the three views the loop
needs:

  extract-result FILE   Print the run's final result text to stdout. The
                        wrapper redirects this into ``agent_stdout.log`` so the
                        old plain-text-mode continuity (a human-readable final
                        message) survives the switch to stream-json.

  metrics FILE          Emit run_metrics.json: total_cost_usd, num_turns and
                        duration from the terminal ``result`` event; per-type
                        token counts (input/output/cache_creation/cache_read)
                        summed over the per-turn usage events; session_id from
                        the ``init`` event; plus the ``claude --version`` string
                        the wrapper records (a stream-json shape-drift canary)
                        and the wrapper wall-clock seconds.

  summarize FILE        A turn-by-turn replay timeline (tool calls + truncated
                        results + assistant text) — the "judge the path" view.

Robustness contract (Cross-cutting): the parser NEVER crashes on a malformed
or mid-stream-truncated trace. It parses what it can, counts parse errors, and
sets ``truncated`` when the final line is unparseable or no terminal ``result``
event was seen. Every subcommand exits 0 on a truncated file — a cut trace is a
reportable condition, not a tool failure.

Token summing note: in stream-json a single assistant message is emitted as
several ``assistant`` lines (one per streamed content block) that repeat one
``message.id``; usage is therefore summed per distinct message id. The streamed
``output_tokens`` are early snapshots and under-report vs. the terminal
aggregate — input/cache totals match the aggregate exactly, which is what makes
this the incrementally-computable number the H2 token breaker will also use.

Usage:
    trajectory_tools.py extract-result FILE
    trajectory_tools.py metrics FILE [--claude-version V] [--duration-secs N]
                                     [-o OUT]
    trajectory_tools.py summarize FILE
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOKEN_FIELDS: dict[str, str] = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_creation": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}


@dataclass
class Parsed:
    """The outcome of parsing a trajectory.jsonl (possibly truncated)."""

    events: list[dict[str, Any]] = field(default_factory=list)
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    parse_errors: int = 0
    truncated: bool = False


def parse(path: str | Path) -> Parsed:
    """Parse a stream-json trace, degrading gracefully on truncation.

    A line that fails ``json.loads`` is counted (``parse_errors``) and skipped
    — it does not raise. ``truncated`` is set when any line failed to parse or
    when the trace has no terminal ``result`` event (an incomplete run).
    """
    out = Parsed()
    try:
        raw = Path(path).read_text()
    except OSError:
        # A missing/unreadable file is the most truncated case of all.
        out.truncated = True
        out.parse_errors += 1
        return out

    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            out.parse_errors += 1
            continue
        if not isinstance(evt, dict):
            out.parse_errors += 1
            continue
        out.events.append(evt)
        etype = evt.get("type")
        if (
            etype == "system"
            and evt.get("subtype") == "init"
            and out.init is None
        ):
            out.init = evt
        elif etype == "result":
            out.result = evt

    out.truncated = out.parse_errors > 0 or out.result is None
    return out


def _assistant_messages(parsed: Parsed) -> list[dict[str, Any]]:
    """Distinct assistant messages, keyed by message id.

    In stream-json a single message is emitted as several ``assistant`` lines
    that share one ``message.id``, each carrying one streamed content block and
    an identical ``usage`` snapshot. We reconstruct each message by taking the
    usage once (first occurrence) and concatenating the per-event content
    blocks, so both usage summing and text extraction see the full message.
    """
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anon = 0
    for evt in parsed.events:
        if evt.get("type") != "assistant":
            continue
        msg = evt.get("message")
        if not isinstance(msg, dict):
            continue
        mid = msg.get("id")
        if isinstance(mid, str):
            key = mid
        else:  # no id (shouldn't happen) -> treat each event as distinct
            anon += 1
            key = f"__anon_{anon}"
        if key not in by_id:
            merged = dict(msg)
            merged["content"] = list(msg.get("content") or [])
            by_id[key] = merged
            order.append(key)
        else:
            extra = msg.get("content")
            if isinstance(extra, list):
                by_id[key]["content"].extend(extra)
    return [by_id[k] for k in order]


def _session_id(parsed: Parsed) -> str | None:
    if parsed.init and isinstance(parsed.init.get("session_id"), str):
        return parsed.init["session_id"]
    # Fall back to any event carrying a session_id (truncated before init is
    # rare, but the result/assistant events echo it).
    for evt in parsed.events:
        sid = evt.get("session_id")
        if isinstance(sid, str):
            return sid
    return None


def compute_metrics(
    path: str | Path,
    *,
    claude_version: str | None = None,
    duration_secs: int | None = None,
) -> dict[str, Any]:
    """Compute run_metrics.json contents from a trajectory."""
    parsed = parse(path)

    totals = {name: 0 for name in TOKEN_FIELDS}
    for msg in _assistant_messages(parsed):
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        for name, api_key in TOKEN_FIELDS.items():
            val = usage.get(api_key)
            if isinstance(val, int):
                totals[name] += val

    result = parsed.result or {}
    return {
        "session_id": _session_id(parsed),
        "claude_version": claude_version,
        "total_cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "wrapper_duration_secs": duration_secs,
        "token_totals": totals,
        "usage_event_count": len(_assistant_messages(parsed)),
        "parse_errors": parsed.parse_errors,
        "truncated": parsed.truncated,
    }


def _text_blocks(msg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    out.append(text)
    return out


def extract_result(path: str | Path) -> str:
    """Return the run's final result text (for agent_stdout.log continuity).

    Prefers the terminal ``result`` event's ``result`` field. If the trace was
    truncated before that event, falls back to the text blocks of the last
    assistant message that produced any — never raises.
    """
    parsed = parse(path)
    if parsed.result is not None:
        text = parsed.result.get("result")
        if isinstance(text, str):
            return text
    # Fallback: last assistant message carrying text.
    for msg in reversed(_assistant_messages(parsed)):
        blocks = _text_blocks(msg)
        if blocks:
            return "\n".join(blocks)
    return ""


def _truncate(text: str, limit: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit] + f"... [+{len(text) - limit} chars]"
    return text


def _stringify_tool_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content)


def render_summary(path: str | Path) -> str:
    """Render a turn-by-turn replay timeline of the trajectory."""
    parsed = parse(path)
    lines: list[str] = []

    sid = _session_id(parsed)
    model = parsed.init.get("model") if parsed.init else None
    lines.append("=" * 72)
    lines.append(f"Trajectory replay  session={sid}  model={model}")
    if parsed.truncated:
        lines.append(
            f"** TRUNCATED trace: parse_errors={parsed.parse_errors}, "
            f"result_event={'present' if parsed.result else 'MISSING'} **"
        )
    lines.append("=" * 72)

    turn = 0
    for evt in parsed.events:
        etype = evt.get("type")
        if etype == "assistant":
            msg = evt.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = _truncate(str(block.get("text", "")))
                    if txt:
                        turn += 1
                        lines.append(f"\n[turn {turn}] assistant: {txt}")
                elif btype == "thinking":
                    turn += 1
                    lines.append(f"\n[turn {turn}] (thinking)")
                elif btype == "tool_use":
                    turn += 1
                    name = block.get("name", "?")
                    tin = _truncate(json.dumps(block.get("input", {})), 160)
                    lines.append(f"\n[turn {turn}] tool_use {name}: {tin}")
        elif etype == "user":
            msg = evt.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                ):
                    res = _truncate(
                        _stringify_tool_result(block.get("content"))
                    )
                    flag = " [is_error]" if block.get("is_error") else ""
                    lines.append(f"    -> result{flag}: {res}")

    m = compute_metrics(path)
    lines.append("\n" + "-" * 72)
    lines.append(
        "run: "
        f"num_turns={m['num_turns']} "
        f"cost_usd={m['total_cost_usd']} "
        f"duration_ms={m['duration_ms']} "
        f"tokens={m['token_totals']} "
        f"truncated={m['truncated']}"
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser(
        "extract-result", help="print final result text (agent_stdout.log)"
    )
    p_extract.add_argument("file")

    p_metrics = sub.add_parser("metrics", help="emit run_metrics.json")
    p_metrics.add_argument("file")
    p_metrics.add_argument("--claude-version", default=None)
    p_metrics.add_argument("--duration-secs", type=int, default=None)
    p_metrics.add_argument(
        "-o", "--output", default=None, help="write JSON here (default stdout)"
    )

    p_summarize = sub.add_parser(
        "summarize", help="turn-by-turn replay timeline"
    )
    p_summarize.add_argument("file")

    args = parser.parse_args(argv)

    if args.cmd == "extract-result":
        text = extract_result(args.file)
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if args.cmd == "metrics":
        metrics = compute_metrics(
            args.file,
            claude_version=args.claude_version,
            duration_secs=args.duration_secs,
        )
        payload = json.dumps(metrics, indent=2)
        if args.output:
            Path(args.output).write_text(payload + "\n")
        else:
            sys.stdout.write(payload + "\n")
        return 0

    if args.cmd == "summarize":
        sys.stdout.write(render_summary(args.file))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())

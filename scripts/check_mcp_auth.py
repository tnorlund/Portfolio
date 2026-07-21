#!/usr/bin/env python3
"""Non-interactive MCP auth/connectivity healthcheck for headless agent runs.

Probes each MCP dependency a headless (``claude -p``) run relies on and
reports auth/connectivity state as one JSON document on stdout:

    {"overall": "healthy|degraded|red", "checks": [{name, status, detail}...]}

Checks
------
claude_oauth   ~/.claude/.credentials.json (the FILE credential store that
               headless runs use -- macOS interactive sessions may use the
               Keychain instead, which headless runs cannot read).  Validates
               ``claudeAiOauth.expiresAt`` against now.  Never prints token
               material: only validity + expiry.
glyph_studio   stdio MCP (node tools/glyph-studio/server/mcp.mjs).  Verifies
               the entry point exists, ``node`` is on PATH, and node_modules
               is installed (tools/glyph-studio/node_modules or
               server/node_modules).  No spawn: cheap and side-effect free.
receipt_tools  Discovered from ~/.claude.json (project entry for the repo
               first, then global) or the repo's .mcp.json.  For an HTTP
               server: one authed POST to its URL if a stored OAuth token
               exists in the file credential store, else an unauthenticated
               probe to distinguish needs_auth (server answered 401/403)
               from unreachable.  For a stdio server: script existence only
               (no OAuth involved).

Exit codes: 0 healthy, 1 degraded, 2 red.  Read-only: never mutates state,
never triggers an interactive auth flow.

Plan: ~/.claude/plans/humble-skipping-quilt.md (W1).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OK_STATUSES = {"ok", "authenticated", "configured_stdio"}
RED_STATUSES = {"missing", "expired", "malformed"}
# Anything else (needs_auth, unreachable, unknown-needs-interactive-check,
# missing_deps, misconfigured, not_configured, ...) degrades the run but a
# headless session can still start.

PROBE_TIMEOUT_S = 10


# --------------------------------------------------------------------------
# Pure logic (unit-tested; no filesystem, no network)
# --------------------------------------------------------------------------


def check_claude_oauth(creds: dict | None, now_ms: int) -> dict:
    """Classify the Claude OAuth file credential. ``creds`` is the parsed
    ~/.claude/.credentials.json content, or None if the file is absent or
    unparseable.  Never surfaces token material."""
    name = "claude_oauth"
    if creds is None:
        return {
            "name": name,
            "status": "missing",
            "detail": (
                "~/.claude/.credentials.json not found or unreadable; "
                "headless runs need the file credential store "
                "(interactive macOS sessions may be using the Keychain)"
            ),
        }
    oauth = creds.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return {
            "name": name,
            "status": "malformed",
            "detail": "credentials file has no claudeAiOauth section",
        }
    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return {
            "name": name,
            "status": "malformed",
            "detail": "claudeAiOauth.expiresAt missing or non-numeric",
        }
    expires_iso = datetime.fromtimestamp(
        expires_at / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    if expires_at <= now_ms:
        return {
            "name": name,
            "status": "expired",
            "detail": f"OAuth expired at {expires_iso}",
        }
    days_left = (expires_at - now_ms) / 86_400_000
    sub = oauth.get("subscriptionType", "unknown")
    return {
        "name": name,
        "status": "ok",
        "detail": (
            f"OAuth valid, expires {expires_iso} "
            f"({days_left:.1f} days; subscription={sub})"
        ),
    }


def find_mcp_oauth_token(creds: dict | None, server_name: str) -> dict | None:
    """Find a stored MCP OAuth entry for ``server_name`` in the file
    credential store (``mcpOAuth`` section, keys contain the server name).
    Returns the entry dict or None."""
    if not isinstance(creds, dict):
        return None
    section = creds.get("mcpOAuth")
    if not isinstance(section, dict):
        return None
    for key, entry in section.items():
        if server_name in key and isinstance(entry, dict):
            return entry
    return None


def classify_receipt_tools_http(
    http_status: int | None, has_token: bool
) -> tuple[str, str]:
    """Map an HTTP probe outcome to (status, detail fragment).

    ``http_status`` is the response code, or None when the request failed at
    the network layer (DNS/conn/timeout)."""
    if http_status is None:
        return "unreachable", "network error probing MCP endpoint"
    if http_status in (401, 403):
        if has_token:
            return (
                "needs_auth",
                f"stored token rejected (HTTP {http_status}); "
                "interactive re-auth required",
            )
        return (
            "needs_auth",
            f"server reachable but no stored OAuth token "
            f"(HTTP {http_status}); interactive auth required",
        )
    if 200 <= http_status < 300:
        if has_token:
            return "authenticated", f"authed probe OK (HTTP {http_status})"
        # Endpoint answered 2xx without auth -- unexpected for a protected
        # gateway; report honestly rather than claiming authenticated.
        return (
            "unknown-needs-interactive-check",
            f"unauthenticated probe returned HTTP {http_status}; "
            "cannot confirm auth state non-interactively",
        )
    return (
        "unknown-needs-interactive-check",
        f"unexpected HTTP {http_status} from MCP endpoint",
    )


def aggregate(checks: list[dict]) -> str:
    """healthy | degraded | red from individual check statuses."""
    statuses = {c.get("status") for c in checks}
    if statuses & RED_STATUSES:
        return "red"
    if statuses - OK_STATUSES:
        return "degraded"
    return "healthy"


def overall_exit_code(overall: str) -> int:
    return {"healthy": 0, "degraded": 1}.get(overall, 2)


def discover_mcp_server(
    claude_config: dict | None, server_name: str, repo_root: Path
) -> dict | None:
    """Find ``server_name`` in parsed ~/.claude.json: the project entry whose
    path contains the repo root wins, then any Portfolio-ish project entry,
    then the global mcpServers section."""
    if not isinstance(claude_config, dict):
        return None
    projects = claude_config.get("projects", {})
    repo_str = str(repo_root)
    candidates: list[dict] = []
    if isinstance(projects, dict):
        for path, proj in projects.items():
            cfg = (proj or {}).get("mcpServers", {}).get(server_name)
            if isinstance(cfg, dict):
                if path == repo_str:
                    return cfg
                candidates.append(cfg)
    global_cfg = (claude_config.get("mcpServers") or {}).get(server_name)
    if isinstance(global_cfg, dict):
        return global_cfg
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# IO wrappers
# --------------------------------------------------------------------------


def load_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def probe_http(url: str, token: str | None, timeout: int) -> int | None:
    """One POST to an MCP endpoint (JSON-RPC initialize). Returns the HTTP
    status code, or None on a network-layer failure.  Read-only from the
    server's perspective and never opens an interactive flow."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "check_mcp_auth", "version": "1.0"},
            },
        }
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def check_glyph_studio(
    claude_config: dict | None, repo_root: Path
) -> dict:
    name = "glyph_studio"
    cfg = discover_mcp_server(claude_config, "glyph-studio", repo_root)
    entry = None
    if cfg and cfg.get("type") == "stdio":
        for a in cfg.get("args", []):
            if str(a).endswith("mcp.mjs"):
                entry = Path(a)
                break
    if entry is None or not entry.exists():
        entry = repo_root / "tools" / "glyph-studio" / "server" / "mcp.mjs"
    if not entry.exists():
        return {
            "name": name,
            "status": "missing",
            "detail": f"server entry point not found: {entry}",
        }
    problems = []
    if shutil.which("node") is None:
        problems.append("node not on PATH")
    pkg_root = entry.parent.parent  # tools/glyph-studio
    if not (
        (pkg_root / "node_modules").is_dir()
        or (entry.parent / "node_modules").is_dir()
    ):
        problems.append(
            f"node_modules not installed (run npm install in {pkg_root})"
        )
    if problems:
        return {
            "name": name,
            "status": "missing_deps",
            "detail": f"{entry}: " + "; ".join(problems),
        }
    return {
        "name": name,
        "status": "ok",
        "detail": f"stdio server ready: node {entry}",
    }


def check_receipt_tools(
    claude_config: dict | None,
    creds: dict | None,
    repo_root: Path,
    timeout: int,
) -> dict:
    name = "receipt_tools"
    cfg = discover_mcp_server(claude_config, "receipt-tools", repo_root)
    if cfg is None:
        mcp_json = load_json(repo_root / ".mcp.json")
        if mcp_json:
            cfg = (mcp_json.get("mcpServers") or {}).get("receipt-tools")
    if not isinstance(cfg, dict):
        return {
            "name": name,
            "status": "not_configured",
            "detail": "no receipt-tools entry in ~/.claude.json or .mcp.json",
        }
    ctype = cfg.get("type", "stdio")
    if ctype == "stdio":
        script = next(
            (a for a in cfg.get("args", []) if str(a).endswith(".py")), None
        )
        if script and Path(script).exists():
            return {
                "name": name,
                "status": "configured_stdio",
                "detail": (
                    f"stdio server (no OAuth): {script} present; "
                    "auth = local AWS credentials"
                ),
            }
        return {
            "name": name,
            "status": "misconfigured",
            "detail": f"stdio server script not found: {script}",
        }
    if ctype in ("http", "sse"):
        url = cfg.get("url")
        if not url:
            return {
                "name": name,
                "status": "misconfigured",
                "detail": f"{ctype} server has no url",
            }
        entry = find_mcp_oauth_token(creds, "receipt-tools")
        token = entry.get("accessToken") if entry else None
        http_status = probe_http(url, token, timeout)
        status, detail = classify_receipt_tools_http(
            http_status, has_token=bool(token)
        )
        return {"name": name, "status": status, "detail": f"{url}: {detail}"}
    return {
        "name": name,
        "status": "unknown-needs-interactive-check",
        "detail": f"unhandled server type {ctype!r}; probe it interactively",
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run(home: Path, repo_root: Path, timeout: int) -> dict:
    creds = load_json(home / ".claude" / ".credentials.json")
    claude_config = load_json(home / ".claude.json")
    now_ms = int(time.time() * 1000)
    checks = [
        check_claude_oauth(creds, now_ms),
        check_glyph_studio(claude_config, repo_root),
        check_receipt_tools(claude_config, creds, repo_root, timeout),
    ]
    return {
        "overall": aggregate(checks),
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "home": str(home),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="credential-store home (default: $HOME; override for testing)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Portfolio repo root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=PROBE_TIMEOUT_S,
        help="HTTP probe timeout, seconds",
    )
    args = parser.parse_args(argv)
    report = run(args.home, args.repo_root, args.timeout)
    print(json.dumps(report, indent=2))
    return overall_exit_code(report["overall"])


if __name__ == "__main__":
    sys.exit(main())

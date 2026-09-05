"""Loopback-only HTTP transport for the local planner web app."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from planner.errors import Conflict, ValidationError
from planner.runtime import configured_planner, context

ORIGINS = {
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (3000, 3400)
}


def make_handler(planner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def respond(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            origin = self.headers.get("Origin")
            if origin in ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = urlparse("http://" + self.headers.get("Host", "")).hostname
            if host not in {"127.0.0.1", "localhost"}:
                self.respond(403, {"error": "Unrecognized host."})
                return False
            if self.headers.get("Origin") not in ORIGINS | {None}:
                self.respond(
                    403, {"error": "This origin cannot access the planner."}
                )
                return False
            return True

        def do_OPTIONS(self):
            if self.allowed():
                self.respond(200, {})

        def do_GET(self):
            if not self.allowed():
                return
            try:
                path = urlparse(self.path).path
                if path == "/planner/api/snapshot":
                    self.respond(200, {**planner.snapshot(), **context()})
                elif path == "/planner/api/clock":
                    self.respond(
                        200,
                        {"version": planner.store.get_clock(), **context()},
                    )
                else:
                    self.respond(404, {"error": "Unknown planner route."})
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except Exception:
                self.respond(
                    503,
                    {
                        "error": "Planner storage is unavailable. Check the local DynamoDB process."
                    },
                )

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != "/planner/api/commands":
                self.respond(404, {"error": "Unknown planner route."})
                return
            try:
                if (
                    self.headers.get("Content-Type", "").split(";")[0]
                    != "application/json"
                ):
                    raise ValidationError("Send application/json.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 70000:
                    raise ValidationError("The request is empty or too large.")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or not isinstance(
                    body.get("request_id"), str
                ):
                    raise ValidationError("Include a request_id and command.")
                response = planner.execute(
                    body.get("command"), body["request_id"]
                )
                self.respond(200, {**response, **context()})
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except (ValidationError, ValueError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(
                    503,
                    {
                        "error": "Could not confirm this change. Retry with the same request id."
                    },
                )

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--create-local-table", action="store_true")
    parser.add_argument(
        "--demo-week", help="Explicitly load a fictional example week"
    )
    args = parser.parse_args()
    try:
        planner = configured_planner(args.create_local_table)
        if args.demo_week:
            from planner.demo import seed_demo

            seed_demo(planner, args.demo_week)
    except ValidationError as exc:
        parser.exit(2, str(exc) + "\n")
    print(
        f"Planner API on http://127.0.0.1:{args.port} ({context()})",
        flush=True,
    )
    ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(planner)
    ).serve_forever()


if __name__ == "__main__":
    main()

import json
import os
import urllib.request


def lambda_handler(event, _context):
    url = os.environ.get("EXTERNAL_API_URL")
    if not url:
        return {
            "statusCode": 200,
            "body": json.dumps({"ok": True, "echo": event}),
        }

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            body = resp.read().decode("utf-8", errors="ignore")
            return {
                "statusCode": resp.status,
                "body": json.dumps(
                    {"ok": True, "payload": body, "input": event}
                ),
            }
    except Exception as e:
        return {
            "statusCode": 502,
            "body": json.dumps({"ok": False, "error": str(e), "input": event}),
        }

import json
import os
import time
import urllib.request


def lambda_handler(event, _context):
    # Expect env CHROMA_HTTP_ENDPOINT like host:port
    endpoint = os.environ["CHROMA_HTTP_ENDPOINT"]
    url = f"http://{endpoint}/api/v1/heartbeat"

    timeout_seconds = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "60"))
    interval_seconds = int(os.environ.get("WAIT_INTERVAL_SECONDS", "3"))

    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
                if resp.status == 200:
                    return {
                        "statusCode": 200,
                        "body": json.dumps({"ok": True}),
                    }
        except Exception:
            pass
        time.sleep(interval_seconds)

    return {
        "statusCode": 504,
        "body": json.dumps({"ok": False, "error": "timeout"}),
    }

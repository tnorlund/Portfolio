import json
import os
import socket
import time
import traceback
import urllib.error
import urllib.request

import boto3


def lambda_handler(event, _context):
    # Expect env CHROMA_HTTP_ENDPOINT like host:port
    endpoint = os.environ["CHROMA_HTTP_ENDPOINT"]
    url = f"http://{endpoint}/api/v1/heartbeat"
    print(f"[wait_handler] Target endpoint: {url}")

    timeout_seconds = int(os.environ.get("WAIT_TIMEOUT_SECONDS", "60"))
    interval_seconds = int(os.environ.get("WAIT_INTERVAL_SECONDS", "3"))

    start = time.time()
    # Pre-flight: ECS discovery
    ecs_cluster = os.environ.get("ECS_CLUSTER_ARN")
    ecs_service = os.environ.get("ECS_SERVICE_ARN")
    print(f"[wait_handler] ECS_CLUSTER_ARN={ecs_cluster}")
    print(f"[wait_handler] ECS_SERVICE_ARN={ecs_service}")

    task_ip = None
    try:
        ecs = boto3.client("ecs")
        # List running tasks for the service
        lt = ecs.list_tasks(
            cluster=ecs_cluster,
            serviceName=ecs_service,
            desiredStatus="RUNNING",
        )
        print(
            f"[wait_handler] list_tasks arnCount={len(lt.get('taskArns', []))}"
        )
        if lt.get("taskArns"):
            dt = ecs.describe_tasks(cluster=ecs_cluster, tasks=lt["taskArns"])
            # Pull first attachment ENI IP
            for t in dt.get("tasks", []):
                for a in t.get("attachments", []):
                    if a.get("type") == "ElasticNetworkInterface":
                        details = {
                            d["name"]: d["value"] for d in a.get("details", [])
                        }
                        private_ip = details.get("privateIPv4Address")
                        if private_ip:
                            task_ip = private_ip
                            print(
                                f"[wait_handler] Found task private IP: {task_ip}"
                            )
                            break
                if task_ip:
                    break
    except Exception as e:
        print(f"[wait_handler] ECS discovery error: {e}")

    # Pre-flight: DNS resolution and raw TCP check
    try:
        host, port_str = endpoint.split(":")
        port = int(port_str)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        addrs = list({info[4][0] for info in infos})
        print(f"[wait_handler] DNS resolved {host}:{port} -> {addrs}")
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                print("[wait_handler] TCP connect OK")
        except Exception as e:
            print(f"[wait_handler] TCP connect error: {e}")
    except Exception as e:
        print(f"[wait_handler] DNS resolution error: {e}")

    attempt = 0
    while time.time() - start < timeout_seconds:
        attempt += 1
        print(f"[wait_handler] Attempt {attempt}...")

        heartbeat_url = (
            f"http://{task_ip}:{port}/api/v1/heartbeat" if task_ip else url
        )
        version_url = (
            f"http://{task_ip}:{port}/api/v1/version"
            if task_ip
            else f"http://{endpoint}/api/v1/version"
        )

        for candidate in [heartbeat_url, version_url]:
            try:
                print(f"[wait_handler] Probing: {candidate}")
                with urllib.request.urlopen(
                    candidate, timeout=5
                ) as resp:  # nosec B310
                    print(f"[wait_handler] HTTP {resp.status} for {candidate}")
                    if resp.status == 200:
                        return {
                            "statusCode": 200,
                            "body": json.dumps({"ok": True}),
                        }
            except urllib.error.HTTPError as he:
                print(
                    f"[wait_handler] HTTPError {he.code} for {candidate}: {he}"
                )
                if he.code == 410 and candidate.endswith("/heartbeat"):
                    print(
                        "[wait_handler] Treating 410 Gone on /heartbeat as healthy"
                    )
                    return {
                        "statusCode": 200,
                        "body": json.dumps({"ok": True}),
                    }
            except Exception as e:
                print(f"[wait_handler] Error for {candidate}: {e}")
                print(traceback.format_exc())

        time.sleep(interval_seconds)

    return {
        "statusCode": 504,
        "body": json.dumps({"ok": False, "error": "timeout"}),
    }

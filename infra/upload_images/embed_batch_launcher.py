import json
import os
from typing import Any, Dict, List

import boto3


_sfn = boto3.client("stepfunctions")


def handler(event: Dict[str, Any], _ctx: Any):
    # Convert an SQS batch into a single SFN execution with items array
    items: List[Dict[str, Any]] = []
    for rec in event.get("Records", []) or []:
        body = rec.get("body")
        payload = json.loads(body) if isinstance(body, str) else body
        if not payload:
            continue
        items.append(payload)

    if not items:
        return {"started": False, "reason": "no items"}

    sfn_arn = os.environ["EMBED_SFN_ARN"]
    _sfn.start_execution(
        stateMachineArn=sfn_arn, input=json.dumps({"items": items})
    )
    return {"started": True, "count": len(items)}

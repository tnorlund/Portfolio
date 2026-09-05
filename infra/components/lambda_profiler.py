"""Dev-only, request-gated cProfile capture for zip Lambda handlers.

The helper has a deliberately tiny import surface. Profiling dependencies are
loaded only after a request opts in with ``__profile=1``. The compressed
``pstats`` payload is emitted in bounded CloudWatch log chunks so the normal
handler package needs no profiler dependency or writable storage outside
Lambda's ``/tmp`` directory.
"""

import os

PROFILE_MARKER = "LAMBDA_PROFILE_CHUNK "
PROFILE_QUERY_PARAM = "__profile"
PROFILE_CHUNK_SIZE = 48_000

_profile_captured = False


def _requested(event):
    if not isinstance(event, dict):
        return False
    params = event.get("queryStringParameters") or {}
    return isinstance(params, dict) and params.get(PROFILE_QUERY_PARAM) == "1"


def _emit_profile(profiler, context):
    # Imports here do not pollute the Lambda INIT profile or the handler's
    # cProfile sample.
    import base64
    import gzip
    import json
    import tempfile
    import uuid

    request_id = getattr(context, "aws_request_id", None)
    profile_id = request_id or str(uuid.uuid4())
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".prof", delete=False) as file:
            path = file.name
        profiler.dump_stats(path)
        with open(path, "rb") as file:  # pylint: disable=unspecified-encoding
            payload = base64.b64encode(gzip.compress(file.read())).decode(
                "ascii"
            )
        total = max(
            1,
            (len(payload) + PROFILE_CHUNK_SIZE - 1) // PROFILE_CHUNK_SIZE,
        )
        for sequence in range(total):
            start = sequence * PROFILE_CHUNK_SIZE
            message = {
                "event": "lambda_profile_chunk",
                "profile_id": profile_id,
                "sequence": sequence,
                "total": total,
                "encoding": "gzip+base64+pstats",
                "data": payload[start : start + PROFILE_CHUNK_SIZE],
            }
            print(PROFILE_MARKER + json.dumps(message), flush=True)
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass


def profile_handler(handler):
    """Profile the first explicitly requested invocation in an environment."""
    if os.environ.get("LAMBDA_PROFILE_ENABLED") != "1":
        return handler

    def wrapped(event, context):
        global _profile_captured
        if _profile_captured or not _requested(event):
            return handler(event, context)

        # Claim the one capture before running the handler so exceptions cannot
        # repeatedly generate large profiles in the same execution environment.
        _profile_captured = True
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()
        try:
            return handler(event, context)
        finally:
            profiler.disable()
            try:
                _emit_profile(profiler, context)
            except Exception as error:  # pragma: no cover - Lambda safeguard
                print(f"LAMBDA_PROFILE_ERROR {error!r}", flush=True)

    return wrapped

from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.embedding.word import list_pending_embedding_batches

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def list_handler(event, context):
    logger.info("Starting list_pending_batches_for_polling_handler")
    summaries = list_pending_embedding_batches()
    logger.info(f"Found {len(summaries)} pending batches")
    return {
        "statusCode": 200,
        "body": [
            {"batch_id": b.batch_id, "openai_batch_id": b.openai_batch_id}
            for b in summaries
        ],
    }

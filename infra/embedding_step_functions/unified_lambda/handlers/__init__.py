"""Unified Lambda handlers for embedding step functions."""

from .base import BaseLambdaHandler
from .compaction import CompactionHandler
from .find_unembedded import FindUnembeddedHandler
from .line_polling import LinePollingHandler
from .list_pending import ListPendingHandler
from .submit_openai import SubmitOpenAIHandler
from .word_polling import WordPollingHandler

__all__ = [
    "BaseLambdaHandler",
    "WordPollingHandler",
    "LinePollingHandler",
    "CompactionHandler",
    "FindUnembeddedHandler",
    "SubmitOpenAIHandler",
    "ListPendingHandler",
]

# Handler registry for easy lookup
HANDLER_REGISTRY = {
    "word_polling": WordPollingHandler,
    "line_polling": LinePollingHandler,
    "compaction": CompactionHandler,
    "find_unembedded": FindUnembeddedHandler,
    "submit_openai": SubmitOpenAIHandler,
    "list_pending": ListPendingHandler,
}

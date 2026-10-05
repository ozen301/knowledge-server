"""Restricted stderr logging for the standalone HTTP process."""

import logging
import sys

from knowledge_server.adapter.server import TOOL_FAILURE_FORMAT

REQUEST_FORMAT = "request method=%s status=%d latency_ms=%.3f"
STARTUP_FORMAT = "startup category=%s"
EVENT_FORMAT = "event category=%s"


class _HTTPLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Handler filters apply to propagated child records, unlike root filters.
        if (
            record.name == "knowledge_server.adapter.server"
            and record.msg == TOOL_FAILURE_FORMAT
        ):
            # The tool name, exception type, and location stay in stdio logs.
            record.msg = EVENT_FORMAT
            record.args = ("tool-failed",)
        elif record.name != "knowledge_server.http" or record.msg not in (
            REQUEST_FORMAT,
            STARTUP_FORMAT,
            EVENT_FORMAT,
        ):
            return False
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_http_logging() -> None:
    """Let the process log only fixed HTTP startup, event, and request lines.

    The handlers of existing loggers are removed, and their records go to one
    stderr handler on the root logger. That handler passes only the startup,
    event, and request lines from `knowledge_server.http`, with no exception
    details. It turns the tool adapter's unexpected-failure record into the
    fixed `tool-failed` event and drops all other records, such as dependency
    or other tool diagnostics, because they can contain tokens, headers,
    queries, or note text. This changes logging for the whole process, so
    only the HTTP launcher calls it; stdio has its own logging setup.
    """
    sink = logging.StreamHandler(sys.stderr)
    sink.addFilter(_HTTPLogFilter())
    sink.setFormatter(logging.Formatter("knowledge-server-http %(message)s"))
    root = logging.getLogger()
    root.handlers = [sink]
    root.setLevel(logging.INFO)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.handlers = []
            logger.propagate = True
    logging.getLogger("knowledge_server.http").setLevel(logging.INFO)

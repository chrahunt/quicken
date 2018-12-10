import logging
import sys
from typing import TextIO


def reset_loggers(stdout: TextIO, stderr: TextIO) -> None:
    """Reset any StreamHandlers on any configured loggers.
    Args:
        stdout the new stdout stream
        stderr the new stderr stream
    """
    def reset_handlers(logger):
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler):
                # Check if using stdout/stderr in underlying stream and call
                # setStream if so.
                # XXX: Use setStream in Python 3.7
                if h.stream == sys.stdout:
                    h.stream = stdout
                elif h.stream == sys.stderr:
                    h.stream = stderr

    # For 'manager' property.
    # noinspection PyUnresolvedReferences
    loggers = logging.Logger.manager.loggerDict
    for _, item in loggers.items():
        if isinstance(item, logging.PlaceHolder):
            # These don't have their own handlers.
            continue
        reset_handlers(item)
    reset_handlers(logging.getLogger())

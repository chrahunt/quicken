"""Logging configuration for the quicken CLI and entrypoints.
"""
from __future__ import annotations

import json
import logging
import time

from logging import NullHandler

from .._logging import UTCFormatter
from .._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import List, Optional


logger = logging.getLogger(__name__)


_default_handler = NullHandler()
_new_handler = None
_root_logger = logging.getLogger("quicken")
_root_logger.propagate = False


def default_configuration(log_file=None):
    """Basic all-encompassing configuration used in tests and handlers.

    Raises:
        PermissionDenied if the log file is not writable
    """
    global _new_handler

    reset_configuration()

    # We must have a log file available.
    if not log_file and not _root_logger.hasHandlers():
        _root_logger.addHandler(_default_handler)
        return

    elif _root_logger.hasHandlers():
        return

    # Let exception propagate.
    with open(log_file, "a"):
        pass

    formatter = DefaultSingleLineLogFormatter(["process"])
    _new_handler = logging.FileHandler(log_file, encoding="utf-8")
    _new_handler.setFormatter(formatter)

    # Setting an error handler is nice because our daemon doesn't have a stderr
    # to trace such things to.
    def handle_error(record):
        logger.exception("Logging exception handling %r", record)

    _new_handler.handleError = handle_error
    _root_logger.addHandler(_new_handler)
    _root_logger.setLevel(logging.DEBUG)


def reset_configuration():
    _root_logger.removeHandler(_default_handler)
    _root_logger.removeHandler(_new_handler)


class DefaultSingleLineLogFormatter(UTCFormatter):
    _format = "{asctime}.{msecs:03.0f} {levelname} {name} {message}"
    _date_format = "%Y-%m-%dT%H:%M:%S"

    def __init__(self, rest_attrs: Optional[List[str]] = None):
        """
        Args:
            rest_attrs: attributes from the record that should be included in the
                trailing json data
        """
        super().__init__(self._format, self._date_format, style="{")
        if not rest_attrs:
            self._rest = []
        else:
            self._rest = rest_attrs

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return time.strftime(datefmt, ct)

    def format(self, record):
        s = super().format(record)
        s = s.replace("\n", "\\n")
        d = {}
        for attr in self._rest:
            value = getattr(record, attr, None)
            if value is not None:
                d[attr] = value
        rest = json.dumps(d)
        return f"{s} {rest}"

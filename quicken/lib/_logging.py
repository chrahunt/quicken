import contextvars
import json
import logging
import os
import time

from collections.abc import Mapping
from logging import NullHandler


_default_handler = NullHandler()
_root_logger = logging.getLogger('quicken')
_root_logger.propagate = False


def default_configuration():
    log_file = os.environ.get('QUICKEN_LOG')
    if not log_file and not _root_logger.hasHandlers():
        _root_logger.addHandler(_default_handler)
        return
    elif _root_logger.hasHandlers():
        return

    formatter = DefaultSingleLineLogFormatter(['process'])
    handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setFormatter(formatter)
    _root_logger.addHandler(handler)
    _root_logger.setLevel(logging.DEBUG)


def reset_configuration():
    _root_logger.removeHandler(_default_handler)


class _Context:
    def __init__(self, prefix):
        self._context = contextvars.copy_context()
        self._prefix = prefix

    def __str__(self):
        return ','.join(
            f'{v.name}={v.get(None)}'
            for v in self._context
            if v.name.startswith(self._prefix))


class _ContextProvider(Mapping):
    def __init__(self, prefix):
        self._prefix = prefix

    def __iter__(self):
        return iter(['context'])

    def __getitem__(self, item):
        if item != 'context':
            raise KeyError(item)
        return _Context(self._prefix)

    def __len__(self):
        return 1


class ContextLogger(logging.LoggerAdapter):
    """Provide contextvars.Context as key 'context' on log messages.
    """
    def __init__(self, logger, prefix):
        super().__init__(logger, _ContextProvider(prefix))


class NullContextFilter(logging.Filter):
    def filter(self, record):
        record.context = getattr(record, 'context', '')
        return True


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


class DefaultSingleLineLogFormatter(UTCFormatter):
    _format = "{asctime}.{msecs:03.0f} {levelname} {name} {message} {rest}"
    _date_format = '%Y-%m-%dT%H:%M:%S'

    converter = time.gmtime

    def __init__(self, rest_attrs=None):
        super().__init__(self._format, self._date_format, style='{')
        if not rest_attrs:
            self._rest = []
        else:
            self._rest = rest_attrs

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return time.strftime(datefmt, ct)

    def format(self, record):
        d = {}
        for attr in self._rest:
            value = getattr(record, attr, None)
            if value is not None:
                d[attr] = value
        record.rest = json.dumps(d)
        s = super().format(record)
        return s

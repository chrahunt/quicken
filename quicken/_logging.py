from collections.abc import Mapping
import contextvars
import logging
import time


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


class UTCFormatter(logging.Formatter):
    converter = time.gmtime

import logging
import logging.config
import os
import signal
import sys
import time

from contextlib import contextmanager
from pathlib import Path
from threading import Timer

import pytest

try:
    from tid import gettid
except ImportError:
    import threading

    def gettid():
        return threading.get_ident()


from .utils.pytest import current_test_name


log_file_format = 'logs/{test_case}.log'


def pytest_runtest_setup(item):
    path = Path(log_file_format.format(test_case=item.name)).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)

    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

    class TestNameAdderFilter(logging.Filter):
        def filter(self, record):
            record.test_name = current_test_name()
            record.pid = os.getpid()
            return True

    class TidFilter(logging.Filter):
        def filter(self, record):
            record.tid = gettid()
            return True

    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {
            'test_name': {
                '()': TestNameAdderFilter,
                'name': 'test_name',
            },
            'tid': {
                '()': TidFilter,
                'name': 'tid',
            }
        },
        'formatters': {
            'default': {
                '()': UTCFormatter,
                'format': '#### [{asctime}][{levelname}][{pid}->{tid}][{test_name}->{name}]\n    {message}',
                'style': '{',
            }
        },
        'handlers': {
            'file': {
                '()': 'logging.FileHandler',
                'level': 'DEBUG',
                'filename': str(path),
                'filters': ['test_name', 'tid'],
                'encoding': 'utf-8',
                'formatter': 'default',
            }
        },
        'root': {
            'filters': ['test_name', 'tid'],
            'handlers': ['file'],
            'level': 'DEBUG',
        }
    })


@contextmanager
def blocked_signals():
    old_signals = signal.pthread_sigmask(
        signal.SIG_SETMASK, range(1, signal.NSIG))
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_signals)


def timeout_timer(item, timeout, callback):
    sys.stderr.write('Test timed out\n')
    sys.stderr.flush()
    if callback:
        callback()
    # TODO: Copy backtrace capability from pytest_timeout.py.
    os._exit(1)


# Stand-in for pytest-timeout due to
# https://bitbucket.org/pytest-dev/pytest-timeout/issues/33/signals-should-be-blocked-in-thread-when
# which breaks signal-related test cases.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    marker = item.get_closest_marker('timeout')
    if not marker:
        yield
        return

    timeout = marker.args[0]
    callback = marker.kwargs.get('callback')
    with blocked_signals():
        t = Timer(timeout, timeout_timer, [item, timeout, callback])
        t.start()

    try:
        yield
    finally:
        t.cancel()
        t.join()


def pytest_collection_modifyitems(config, items):
    global log_file_format
    log_file_format = str(Path(log_file_format).absolute())
    # TODO: Use log_file as log_file_format
    # config.config.log_file
    print('pytest_collection_modifyitems()')

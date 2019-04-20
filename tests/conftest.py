import logging
import logging.config
import os
import signal
import sys
import time
import threading
import traceback

from contextlib import contextmanager
from pathlib import Path
from threading import Timer

import py
import pytest

from _pytest.reports import TestReport
from more_itertools import one

try:
    from tid import gettid
except ImportError:
    def gettid():
        return threading.get_ident()

from .utils.pytest import current_test_name


log_file_format = 'logs/{test_case}.log'


PYTEST_TIMEOUT_START = 'pytest_timeout_start'


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
    if callback:
        callback()

    config = item.config
    reporter = item.config.pluginmanager.getplugin('terminalreporter')
    hook = item.ihook

    start_time = one(
        p[1] for p in item.user_properties if p[0] == PYTEST_TIMEOUT_START)
    now = time.time()
    longrepr = '\nTIMEOUT\n\n' + dump_stacks()
    report = TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords={x: 1 for x in item.keywords},
        outcome='failed',
        longrepr=longrepr,
        when='call',
        sections=[],
        duration=now - start_time,
        user_properties=item.user_properties,
    )

    hook.pytest_runtest_logreport(report=report)

    try:
        # XXX: Might race with the main thread here, but no real way to block it.
        config.hook.pytest_terminal_summary(
            terminalreporter=reporter, exitstatus=1, config=config)
        reporter.write_sep("-", "TIMEOUT")
        reporter.summary_stats()
    except Exception:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
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

    # Start time for reporting if needed.
    item.user_properties.append((PYTEST_TIMEOUT_START, time.time()))

    try:
        yield
    finally:
        t.cancel()
        t.join()


def dump_stacks():
    """Dump the stacks of all threads except the current thread"""
    current_ident = threading.current_thread().ident
    text = ''
    for thread_ident, frame in sys._current_frames().items():
        if thread_ident == current_ident:
            continue
        for t in threading.enumerate():
            if t.ident == thread_ident:
                thread_name = t.name
                break
        else:
            thread_name = '<unknown>'
        text += write_title('Stack of %s (%s)' % (thread_name, thread_ident)) + '\n'
        text += ''.join(traceback.format_stack(frame))
    return text


def pytest_collection_modifyitems(config, items):
    global log_file_format
    log_file_format = str(Path(log_file_format).absolute())
    # TODO: Use log_file as log_file_format
    # config.config.log_file
    print('pytest_collection_modifyitems()')


def write_title(title, sep='-'):
    """Write a section title

    If *stream* is None sys.stderr will be used, *sep* is used to
    draw the line.
    """
    width = py.io.get_terminal_width()
    fill = int((width - len(title) - 2) / 2)
    line = ' '.join([sep * fill, title, sep * fill])
    if len(line) < width:
        line += sep * (width - len(line))
    return line


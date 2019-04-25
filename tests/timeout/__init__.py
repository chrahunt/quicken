import logging
import os
import signal
import sys
import time
import threading
import traceback

from contextlib import contextmanager
from threading import Timer

import py
import pytest

from _pytest.reports import TestReport
from more_itertools import one


logger = logging.getLogger(__name__)


PYTEST_TIMEOUT_START = 'pytest_timeout_start'


@contextmanager
def blocked_signals():
    old_signals = signal.pthread_sigmask(
        signal.SIG_SETMASK, range(1, signal.NSIG))
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_signals)


def pytest_addhooks(pluginmanager):
    from . import newhooks
    pluginmanager.add_hookspecs(newhooks)


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'timeout(timeout)'
    )


def timeout_timer(item, timeout):
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

    try:
        hook.pytest_timeout_timeout(item=item, report=report)
    except:
        # Can't let user code interrupt our execution.
        logger.exception('Error running pytest_timeout_timeout handlers')

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
    with blocked_signals():
        t = Timer(timeout, timeout_timer, [item, timeout])
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

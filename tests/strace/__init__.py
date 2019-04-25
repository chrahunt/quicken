"""Run test under strace.
"""
import logging
import os
import subprocess
import time

from pathlib import Path

import pytest

from ..utils.pytest import current_test_name


logger = logging.getLogger(__name__)


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'strace'
    )


def is_traced(pid=None):
    if not pid:
        pid = os.getpid()
    text = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
    start = text.find('TracerPid:\t')
    return text[start + len('TracerPid:\t')] != '0'


def busy_wait(predicate, timeout, interval=0.01):
    start = time.monotonic()
    now = start
    while now - start < timeout:
        if predicate():
            return True
        now = time.monotonic()
        time.sleep(min(interval, now - start))
        now = time.monotonic()
    return predicate()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    marker = item.get_closest_marker('strace')
    if not marker:
        yield
        return

    path = Path(__file__).parent.parent.parent / 'logs' / f'strace.{current_test_name()}.log'
    p = subprocess.Popen(
        ['strace', '-yttfo', str(path), '-s', '512', '-p', str(os.getpid())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    if not busy_wait(is_traced, timeout=5):
        logger.warning('Could not attach strace')
    try:
        yield
    finally:
        p.terminate()
        p.wait()

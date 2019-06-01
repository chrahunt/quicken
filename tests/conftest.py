import logging
import logging.config
import sys
import threading

from pathlib import Path

import pytest

try:
    from tid import gettid
except ImportError:
    def gettid():
        return threading.get_ident()

try:
    from ch.debug.gdb_get_trace import get_process_stack
except ImportError:
    if sys.platform.startswith('win'):
        def get_process_stack(_pid):
            raise NotImplementedError('Not implemented on Windows')
    else:
        raise

from .utils import venv_factory
from .utils.pytest import current_test_name
from .utils.process import disable_child_tracking, kill_children

from quicken.lib._logging import DefaultSingleLineLogFormatter


log_dir = Path(__file__).parent.parent / 'logs'


pytest_plugins = "tests.plugins.timeout", "tests.plugins.strace"


logger = logging.getLogger(__name__)


def get_log_file(test_name=None):
    if not test_name:
        test_name = current_test_name()

    return log_dir / f'{test_name}.log'


@pytest.fixture
def log_file_path():
    return get_log_file()


_last_handler = None


def pytest_runtest_setup(item):
    global _last_handler
    path = get_log_file(item.name)
    path.parent.mkdir(parents=True, exist_ok=True)

    class TestNameAdderFilter(logging.Filter):
        def filter(self, record):
            record.test_name = current_test_name()
            return True

    class TidFilter(logging.Filter):
        def filter(self, record):
            record.tid = gettid()
            return True

    root_logger = logging.getLogger('')
    root_logger.addFilter(TestNameAdderFilter())
    root_logger.addFilter(TidFilter())
    root_logger.setLevel(logging.DEBUG)

    formatter = DefaultSingleLineLogFormatter(['process'])
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    if _last_handler:
        root_logger.removeHandler(_last_handler)
    _last_handler = handler

    logger.info('---------- Starting test ----------')


@pytest.hookimpl
def pytest_timeout_timeout(item, report):
    # Get subprocess stacks.
    stacks = []
    # Prevent race conditions on fork since we spawn other processes to get
    # stacks.
    children = disable_child_tracking()
    for child in children:
        text = f'stack for ({child.pid}): {child.cmdline()}\n'
        try:
            text += get_process_stack(child.pid)
        except Exception as e:
            text += f'Error: {e}'

        stacks.append(text)

    if stacks:
        report.longrepr = report.longrepr + '\nsubprocess stacks:\n' + '\n'.join(stacks)

    kill_children()


@pytest.fixture
def virtualenvs():
    with venv_factory() as factory:
        yield factory

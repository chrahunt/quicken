import logging
import logging.config
import os
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
        def get_process_stack(pid):
            raise NotImplementedError('Not implemented on Windows')
    else:
        raise


from .utils.pytest import current_test_name
from .utils.process import active_children, disable_child_tracking, kill_children

from quicken._logging import UTCFormatter


log_file_format = 'logs/{test_case}.log'


pytest_plugins = "tests.timeout", "tests.strace"


def pytest_runtest_setup(item):
    path = Path(log_file_format.format(test_case=item.name)).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)

    class TestNameAdderFilter(logging.Filter):
        def filter(self, record):
            record.test_name = current_test_name()
            return True

    class PidFilter(logging.Filter):
        def filter(self, record):
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
            'pid': {
                '()': PidFilter,
                'name': 'pid',
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
                'filters': ['test_name', 'tid', 'pid'],
                'encoding': 'utf-8',
                'formatter': 'default',
            }
        },
        'root': {
            'filters': ['test_name', 'tid', 'pid'],
            'handlers': ['file'],
            'level': 'DEBUG',
        }
    })


def pytest_collection_modifyitems(config, items):
    global log_file_format
    log_file_format = str(Path(log_file_format).absolute())
    # TODO: Use log_file as log_file_format
    # config.config.log_file


@pytest.hookimpl
def pytest_timeout_timeout(item, report):
    # Get subprocess stacks.
    stacks = []
    # Prevent race conditions on fork since we spawn other processes to get
    # stacks.
    disable_child_tracking()
    children = active_children()
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

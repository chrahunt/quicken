import logging
import logging.config
import os
from pathlib import Path
import time

import tid

from .utils import current_test_name


def setup_logging(path) -> None:
    path = Path(path).absolute()
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
            record.tid = tid.gettid()
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


log_file_format = 'logs/{test_case}.log'


def pytest_runtest_setup(item):
    #print('pytest_runtest_setup()')
    log_file = log_file_format.format(test_case=item.name)
    setup_logging(log_file)


def pytest_collection_modifyitems(config, items):
    global log_file_format
    log_file_format = str(Path(log_file_format).absolute())
    # TODO: Use log_file as log_file_format
    # config.config.log_file
    print('pytest_collection_modifyitems()')

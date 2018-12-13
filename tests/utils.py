from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import signal
import tempfile
import time
from typing import Any, ContextManager

import psutil


@contextmanager
def chdir(path: Path) -> ContextManager:
    current_path = Path.cwd()
    try:
        os.chdir(str(path))
        yield
    finally:
        os.chdir(str(current_path))


@contextmanager
def isolated_filesystem() -> ContextManager[Path]:
    with tempfile.TemporaryDirectory() as d:
        with chdir(d):
            yield Path(d)


@contextmanager
def patch_attribute(obj: Any, attr: str, new_attr: Any):
    """Patch attribute on an object then revert.
    """
    old_attr = getattr(obj, attr)
    setattr(obj, attr, new_attr)
    new_attr._attr = old_attr
    try:
        yield
    finally:
        setattr(obj, attr, old_attr)


def waitpid():
    """waitpid implementation that works for non-child processes.
    """

@contextmanager
def contained_children(timeout=1) -> ContextManager:
    """Automatically kill any processes forked in this context, for cleanup.
    Timeout is seconds to wait for graceful termination before killing children.
    """
    try:
        yield
    finally:
        procs = psutil.Process().children()
        for p in procs:
            p.terminate()
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        for p in alive:
            p.kill()


def setup_logging() -> None:
    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                '()': UTCFormatter,
                'format': '#### [{asctime}][{levelname}][{name}]\n    {message}',
                'style': '{',
            }
        },
        'handlers': {
            'file': {
                '()': 'logging.FileHandler',
                'level': 'DEBUG',
                'filename': 'pytest.log',
                'encoding': 'utf-8',
                'formatter': 'default',
            }
        },
        'root': {
            'handlers': ['file'],
            'level': 'DEBUG',
        }
    })

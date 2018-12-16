from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, ContextManager, List

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
def env(**kwargs) -> ContextManager:
    """Update environment within context manager.
    """
    def update(target, source):
        updated = {}
        for k, v in source.items():
            if v is None:
                try:
                    updated[k] = target.pop(k)
                except KeyError:
                    pass
            else:
                updated[k] = target.get(k, None)
                target[k] = v
        return updated

    previous_env = update(os.environ, kwargs)
    try:
        yield
    finally:
        update(os.environ, previous_env)


@contextmanager
def argv(args: List[str]) -> ContextManager:
    argv = sys.argv
    sys.argv = args
    try:
        yield
    finally:
        sys.argv = argv


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


_name_re = re.compile(r'(?P<file>.+?)::(?P<name>.+?) \(.*\)$')
def current_test_name():
    name = os.environ['PYTEST_CURRENT_TEST']
    m = _name_re.match(name)
    if not m:
        raise RuntimeError(f'Could not extract name from {name}')
    return m.group('name')


def setup_logging() -> None:
    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

    class TestNameAdderFilter(logging.Filter):
        def filter(self, record):
            record.test_name = os.environ['PYTEST_CURRENT_TEST']
            record.pid = os.getpid()
            return True

    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': True,
        'filters': {
            'test_name': {
                '()': TestNameAdderFilter,
                'name': 'test_name',
            },
        },
        'formatters': {
            'default': {
                '()': UTCFormatter,
                'format': '#### [{asctime}][{levelname}][{pid}][{test_name}->{name}]\n    {message}',
                'style': '{',
            }
        },
        'handlers': {
            'file': {
                '()': 'logging.FileHandler',
                'level': 'DEBUG',
                'filename': 'pytest.log',
                'filters': ['test_name'],
                'encoding': 'utf-8',
                'formatter': 'default',
            }
        },
        'root': {
            'filters': ['test_name'],
            'handlers': ['file'],
            'level': 'DEBUG',
        }
    })

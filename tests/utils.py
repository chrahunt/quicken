from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import re
import socket
import socketserver
import sys
import tempfile
import threading
import time
from typing import Any, ContextManager, List
import uuid

import psutil
import tid


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
    """Update environment only within context manager.

    Args:
        kwargs: Key-value pairs corresponding to environment variables to set in
            the with block. If an argument is set to `None` then the environment
            variable is removed. Only the provided environment variables are
            changed, any changes made to other environment variables in the with
            block are not undone.
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
    """Set argv within the context.
    """
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


class ChildManager:
    """Register children with the eldest parent process.

    We do this instead of recursively getting children with psutil because
    intermediate processes may have already died.
    """
    def __init__(self):
        self._tempdir = tempfile.mkdtemp()
        self._id = str(uuid.uuid4())
        self._socket = f'{self._tempdir}/socket'
        self._mutex = threading.Lock()
        self._children = []
        os.register_at_fork(after_in_child=self.after_in_child)
        self._serve()

    def _serve(self):
        class Server(
            socketserver.UnixStreamServer, socketserver.ThreadingMixIn):
            pass

        class Handler(socketserver.StreamRequestHandler):
            def handle(_self):
                max_pid_len = len(Path('/proc/sys/kernel/pid_max').read_bytes())
                pid = int(_self.request.recv(max_pid_len).decode('utf-8'))
                self.children_append(psutil.Process(pid=pid))

        server = Server(self._socket, Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

    def children_append(self, child):
        with self._mutex:
            self._children.append(child)

    def children_pop_all(self):
        with self._mutex:
            l = self._children
            self._children = []
        return l

    def after_in_child(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self._socket)
            sock.sendall(str(os.getpid()).encode('utf-8'))


child_manager = ChildManager()


@contextmanager
def contained_children(timeout=1) -> ContextManager:
    """Automatically kill any Python processes forked in this context, for
    cleanup. Handles any descendents.

    Timeout is seconds to wait for graceful termination before killing children.
    """
    try:
        yield
    finally:
        procs = child_manager.children_pop_all()
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        for p in alive:
            p.kill()


_name_re = re.compile(r'(?P<file>.+?)::(?P<name>.+?) \(.*\)$')
def current_test_name():
    try:
        name = os.environ['PYTEST_CURRENT_TEST']
    except KeyError:
        return '<outside test>'
    m = _name_re.match(name)
    if not m:
        raise RuntimeError(f'Could not extract name from {name}')
    return m.group('name')


def setup_logging() -> None:
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
                'filename': 'pytest.log',
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

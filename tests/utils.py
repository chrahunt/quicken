from contextlib import contextmanager
import logging
import os
from pathlib import Path
import pickle
import re
import sys
import tempfile
import time
from typing import Any, ContextManager, List

from fasteners import InterProcessLock
import psutil


logger = logging.getLogger(__name__)


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
def umask(umask: int) -> ContextManager:
    """Set umask within the context.
    """
    umask = os.umask(umask)
    try:
        yield
    finally:
        os.umask(umask)


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
    intermediate processes may have already exited.
    """
    def __init__(self):
        self._tempdir = tempfile.mkdtemp()
        self._pidlist = f'{self._tempdir}/pids'
        self._lock = InterProcessLock(self._pidlist)
        self._children = []
        self._num_children = 0
        os.register_at_fork(
            before=self._before_in_parent,
            after_in_parent=self._after_in_parent,
            after_in_child=self._after_in_child)

    def children_pop_all(self):
        with self._mutex:
            l = self._children
            self._children = []
        return l

    def active_children(self):
        with self._mutex:
            return list(self._children)

    @property
    @contextmanager
    def _mutex(self):
        """IPC lock on pidlist and save/load _children member.
        """
        with self._lock:
            contents = Path(self._pidlist).read_bytes()
            if contents:
                self._children = pickle.loads(contents)
            try:
                yield
            finally:
                with open(self._pidlist, 'wb') as f:
                    f.write(pickle.dumps(self._children))
                    f.flush()
                    os.fsync(f.fileno())

    def _children_append(self, child):
        with self._mutex:
            self._children.append(child)

    def _before_in_parent(self):
        with self._mutex:
            self._num_children = len(self._children)

    def _after_in_parent(self):
        # Busy wait for child to write pid.
        while True:
            with self._mutex:
                num_children = len(self._children)
                if num_children != self._num_children:
                    # Child added itself.
                    break
            time.sleep(0.005)

    def _after_in_child(self):
        with self._mutex:
            logger.debug('Child writing process')
            self._children.append(psutil.Process(pid=os.getpid()))


child_manager = ChildManager()


@contextmanager
def contained_children(timeout=1, assert_graceful=True) -> ContextManager:
    """Automatically kill any Python processes forked in this context, for
    cleanup. Handles any descendents.

    Timeout is seconds to wait for graceful termination before killing children.
    """
    try:
        yield child_manager
    finally:
        procs = child_manager.children_pop_all()
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        num_alive = len(alive)
        for p in alive:
            logger.warning('Cleaning up child: %d', p.pid)
            p.kill()
        if assert_graceful:
            assert not num_alive, \
                f'Unexpected children still alive: {alive}'


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

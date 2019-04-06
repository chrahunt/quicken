import errno
import json
import logging
import os
import signal
import sys
import tempfile
import time

from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, List

import psutil

from fasteners import InterProcessLock


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
def preserved_signals() -> ContextManager:
    handlers = [(s, signal.getsignal(s)) for s in range(1, signal.NSIG)]
    try:
        yield
    finally:
        for sig, handler in handlers:
            try:
                signal.signal(sig, handler)
            except TypeError:
                # Can happen if handler is None (seen with signal 32, 33)
                pass
            except OSError as e:
                # SIGKILL/QUIT cannot be set, so just ignore.
                if e.errno != errno.EINVAL:
                    raise


@contextmanager
def captured_std_streams() -> ContextManager:
    """Capture standard streams and provide an interface for interacting with
    them.

    Be careful with returned file objects - if stdout/stderr are read before the
    block has exited it could block. Also if any file descriptors are left open.
    """
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    stdin_old, stdout_old, stderr_old = \
        sys.stdin, sys.stdout, sys.stderr

    sys.stdin, sys.stdout, sys.stderr = \
        os.fdopen(stdin_r), os.fdopen(stdout_w, 'w'), os.fdopen(stderr_w, 'w')
    try:
        yield os.fdopen(stdin_w, 'w'), os.fdopen(stdout_r), os.fdopen(stderr_r)
    finally:
        os.close(stdin_r)
        os.close(stdout_w)
        os.close(stderr_w)
        sys.stdin, sys.stdout, sys.stderr = \
            stdin_old, stdout_old, stderr_old


class ChildManager:
    """Register children with the eldest parent process.

    We do this instead of recursively getting children with psutil because
    intermediate processes may have already exited.
    """
    def __init__(self):
        self._tempdir = tempfile.mkdtemp()
        self._pidlist = f'{self._tempdir}/pids'
        self._lock = InterProcessLock(self._pidlist)
        self._children: List[psutil.Process] = []
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
            contents = Path(self._pidlist).read_text(encoding='utf-8')
            if contents:
                self._children = self._deserialize_children(contents)
            try:
                yield
            finally:
                with open(self._pidlist, 'w', encoding='utf-8') as f:
                    f.write(self._serialize_children())
                    f.flush()
                    os.fsync(f.fileno())

    def _serialize_children(self) -> str:
        out = []
        for child in self._children:
            out.append([child.pid, child.create_time()])
        return json.dumps(out)

    def _deserialize_children(self, s) -> List[psutil.Process]:
        children = json.loads(s)
        out = []
        for pid, create_time in children:
            try:
                process = psutil.Process(pid=pid)
            except psutil.NoSuchProcess:
                pass
            else:
                if process.create_time() == create_time:
                    out.append(process)
        return out

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
        alive = kill_children(timeout)
        num_alive = len(alive)
        # Get current exception - if something was raised we should be raising
        # that.
        # XXX: Need to check use cases to see if there are any cases where
        #  we are expecting an exception outside of the 'contained_children'
        #  block.
        _, exc, _ = sys.exc_info()
        if assert_graceful and exc is None:
            assert not num_alive, \
                f'Unexpected children still alive: {alive}'


def kill_children(timeout=1) -> List[psutil.Process]:
    """
    Kill any active children, returning any that were not terminated within
    timeout.
    Args:
        timeout: time to wait before killing.

    Returns:
        list of processes that had to be killed forcefully.

    """
    procs = child_manager.children_pop_all()
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        logger.warning('Cleaning up child: %d', p.pid)
        p.kill()
    return alive

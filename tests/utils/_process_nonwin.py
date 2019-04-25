import json
import logging
import os
import sys
import tempfile
import time
import threading

from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, List, Set

import psutil

from fasteners import InterProcessLock


logger = logging.getLogger(__name__)


__all__ = [
    'active_children',
    'contained_children',
    'disable_child_tracking',
    'kill_children'
]


class ChildManagerSharedState:
    def __init__(self, base_path):
        self._path = base_path
        Path(self._path).touch()

        lock_file = self._path + '.lock'
        self._lock = InterProcessLock(lock_file)
        self._tlock = threading.RLock()

        self.children: List[psutil.Process] = []
        self.keys: Set[str] = set()

    @property
    @contextmanager
    def lock_guard(self):
        with self._tlock:
            with self._lock:
                self.load()
                try:
                    yield
                finally:
                    self.save()

    # File format is:
    # {
    #   # List of unacknowledged process keys.
    #   "keys": ["ppid[idx]", ...],
    #   # Actual process information as updated by last opening process.
    #   "processes": [[pid, create_time], ...]
    # }

    def load(self):
        contents = Path(self._path).read_text(encoding='utf-8')
        if not contents:
            return

        try:
            data = json.loads(contents)
        except json.JSONDecodeError:
            logger.warning('Bad JSON text: "%r"', contents)
            raise

        self.keys = set(data['keys'])

        self.children = []
        for pid, create_time in data['processes']:
            try:
                process = psutil.Process(pid=pid)
            except psutil.NoSuchProcess:
                pass
            else:
                if process.create_time() == create_time:
                    self.children.append(process)

    def save(self):
        data = {
            'keys': list(self.keys),
            'processes': [
                [child.pid, child.create_time()]
                for child in self.children
            ],
        }

        with open(self._path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(data))
            f.flush()
            os.fsync(f.fileno())


class ChildManager:
    """Register children with the eldest parent process.

    We do this instead of recursively getting children with psutil because
    intermediate processes may have already exited.

    Behavior is undefined if fork occurs:
    - from multiple threads (unless manager is disabled)
    - from within callbacks registered with register_at_fork
    """
    def __init__(self, child_start_timeout: float = 5):
        """
        Args:
            child_start_timeout: timeout (in seconds) to wait for the child process to
              start. "Starting" in this case means the child handler runs.
        """
        # XXX: this directory is leaked
        tempdir = tempfile.mkdtemp()
        self._state = ChildManagerSharedState(f'{tempdir}/pids')
        self._tls = threading.local()
        self._tls.disabled = False
        self._child_start_timeout = child_start_timeout

        self._re_init()

        os.register_at_fork(
            after_in_parent=self._after_in_parent,
            after_in_child=self._after_in_child)

    def children_pop_all(self):
        with self._state.lock_guard:
            l = self._state.children
            self._state.children = []
        return l

    def active_children(self):
        with self._state.lock_guard:
            return self._state.children.copy()

    @property
    def disabled(self) -> bool:
        """Disable tracking child processes spawned from this thread.
        """
        return self._tls.disabled

    @disabled.setter
    def disabled(self, v: bool):
        self._tls.disabled = v

    def _re_init(self):
        """Initialization that happens in each process.
        """
        self._child_index = 0

    def _make_key(self, pid, index):
        return f'{pid}[{index}]'

    def _after_in_child(self):
        with self._state.lock_guard:
            ppid = os.getppid()
            key = self._make_key(ppid, self._child_index)
            self._state.keys.add(key)

            pid = os.getpid()
            process = psutil.Process(pid=pid)
            self._state.children.append(process)

        self._re_init()

    def _after_in_parent(self):
        if self.disabled:
            return

        # Best effort, try to wait for the child to have actually started and
        # written pid, so that if an error occurs we will clean up the process.
        key = self._make_key(os.getpid(), self._child_index)
        start = time.time()
        now = start
        while now - start < self._child_start_timeout:
            with self._state.lock_guard:
                try:
                    self._state.keys.remove(key)
                except KeyError:
                    pass
                else:
                    break

            time.sleep(0.005)
            now = time.time()
        else:
            logger.error(
                'Child did not start in time (started: %d; now: %d)',
                start,
                now
            )

        # Unconditionally update index so next child doesn't conflict.
        self._child_index += 1


child_manager = ChildManager()


def active_children():
    return child_manager.active_children()


@contextmanager
def contained_children(
        timeout=1, assert_graceful=True) -> ContextManager[ChildManager]:
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


def disable_child_tracking():
    child_manager.disabled = True


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

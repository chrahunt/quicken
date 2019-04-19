import json
import logging
import os
import sys
import tempfile
import time

from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, List

import psutil

from fasteners import InterProcessLock


logger = logging.getLogger(__name__)


__all__ = ['contained_children', 'kill_children']


class ChildManager:
    """Register children with the eldest parent process.

    We do this instead of recursively getting children with psutil because
    intermediate processes may have already exited.
    """
    def __init__(self):
        # XXX: this directory is leaked
        tempdir = tempfile.mkdtemp()
        self._pidlist = f'{tempdir}/pids'
        Path(self._pidlist).touch()
        # Use separate dedicated lock file to avoid issues opening/closing.
        lock_file = f'{tempdir}/lock'
        self._lock = InterProcessLock(lock_file)
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
        try:
            children = json.loads(s)
        except json.JSONDecodeError:
            logger.warning('Bad JSON text: "%r"', s)
            raise

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

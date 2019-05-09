"""Utilities for managing child processes within a scope - this ensures
tests run cleanly even on failure and also gives us a mechanism to
get debug info for our children.

This approach creates a separate process that executes ptrace on the original,
keeping track of everything.

Information on traced processes is retrieved by querying the separate process.
"""
import logging
import os
import signal
import sys
import threading

from contextlib import contextmanager
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection
from typing import ContextManager, List

import psutil
import ptrace.debugger
import ptrace.error

#from ptrace.binding.func import PTRACE_O_TRACEEXIT
from ptrace.debugger.process_event import (
    NewProcessEvent, ProcessExecution, ProcessExit
)
from ptrace.debugger import ProcessSignal


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

__all__ = [
    'active_children',
    'contained_children',
    'disable_child_tracking',
    'kill_children'
]


PTRACE_O_EXITKILL = 0x00100000


class Debugger(ptrace.debugger.PtraceDebugger):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.stopping = False

    @property
    def pids(self):
        return list(self.dict.keys())

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, _exc_type, _exc_value, _exc_tb):
        self._lock.release()


def process_events(pid, debugger):
    """Loop for processing events.
    """
    def process_event(pid, status):
        try:
            process = debugger.dict[pid]
        except KeyError:
            logger.warning('Unknown process: %d', pid)
            return

        event = process.processStatus(status)
        signal = 0
        to_continue = [process]

        if isinstance(event, NewProcessEvent):
            logger.debug('[%d] created', event.process.pid)
            to_continue.append(event.process)

        elif isinstance(event, ProcessExit):
            logger.debug('[%d] exit', pid)
            debugger.deleteProcess(process)
            return

        elif isinstance(event, ProcessSignal):
            logger.debug('[%d] signal: %d', pid, event.signum)
            signal = event.signum

        elif isinstance(event, ProcessExecution):
            logger.debug('[%d] execution', pid)

        else:
            logger.debug('Received unknown event: %r (%s)', event, type(event))

        if debugger.stopping:
            return

        for process in to_continue:
            try:
                process.cont(signal)
            except ptrace.error.PtraceError as e:
                if 'No such process' in str(e):
                    logger.warning('Tried to continue missing process %d', process.pid)
                    debugger.deleteProcess(process)

    process = debugger.addProcess(pid, is_attached=False)

    with debugger:
        logger.debug('Before restarting tracee')
        process.cont()
        logger.debug('After restarting tracee')

    while debugger.dict:
        logger.debug('Waiting for process')
        pid, status = os.waitpid(-1, 0)
        with debugger:
            process_event(pid, status)


def self_attach_target(fd):
    def detach():
        # XXX: Should we actually not double-fork here? Then it would kill
        #  everything if top-level process dies.
        pid = os.fork()
        if pid:
            os._exit(0)

        os.setsid()

    detach()

    def make_debugger():
        debugger = Debugger()
        # Avoid extraneous SIGTRAP.
        debugger.traceExec()
        debugger.traceFork()
        debugger.traceClone()
        # PTRACE_EVENT_TRACEEXIT isn't handled properly by python-ptrace.
        #debugger.options |= PTRACE_O_TRACEEXIT | PTRACE_O_EXITKILL
        debugger.options |= PTRACE_O_EXITKILL
        return debugger

    debugger = make_debugger()

    conn = Connection(fd)
    conn.send(os.getpid())
    main_pid = conn.recv()

    t = threading.Thread(target=process_events, args=(main_pid, debugger))
    t.daemon = True
    t.start()

    # Let process proceed.
    conn.send(True)

    # Handle requests.
    while True:
        try:
            req = conn.recv()
        except:
            logger.exception('Received exception on receive')
            sys.exit()

        if req == 'pids':
            with debugger:
                pids = list(debugger.dict.keys())
            conn.send(pids)

        elif req == 'stop':
            # All but the main process.
            with debugger:
                debugger.stopping = True

                for pid, process in debugger.dict.items():
                    if pid != main_pid:
                        process.kill(signal.SIGSTOP)
            conn.send(list(debugger.dict.keys()))

        elif req == 'detach':
            with debugger:
                for _, process in debugger.dict.items():
                    process.detach()
                pids = debugger.pids
            conn.send(pids)


class ChildTracer:
    def __init__(self):
        self._conn, self._child = Pipe()
        p = Process(target=self_attach_target, args=(self._child.fileno(),))
        p.start()
        pid = self._conn.recv()
        logger.debug('Tracer pid: %s', pid)
        # TODO: prctl(PR_SET_PTRACER, pid)
        # if /proc/sys/kernel/yama/ptrace_scope exists and has 1
        self._conn.send(os.getpid())
        # Wait to be ptraced before continuing.
        self._conn.recv()
        logger.debug('Continuing in parent')

    def active_children(self):
        self._conn.send('pids')
        pids = self._conn.recv()
        try:
            i = pids.index(os.getpid())
        except ValueError:
            pass
        else:
            pids.pop(i)
        return [psutil.Process(pid=pid) for pid in pids]

    def children_pop_all(self):
        return self.active_children()

    def send_stop(self):
        self._conn.send('stop')
        return self._conn.recv()

    def detach(self):
        self._conn.send('detach')
        return self._conn.recv()


child_manager = ChildTracer()


def active_children() -> List[psutil.Process]:
    """Returns the active child processes.
    """
    return child_manager.active_children()


@contextmanager
def contained_children(
        timeout=1, assert_graceful=True) -> ContextManager[ChildTracer]:
    """Automatically kill any Python processes forked in this context, for
    cleanup. Handles any descendants.

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
    _pids = child_manager.send_stop()
    # May be different if something was killed.
    pids = child_manager.detach()
    return pids


def kill_children(timeout=1) -> List[psutil.Process]:
    """
    Kill any active children, returning any that were not terminated within
    timeout.
    Args:
        timeout: time to wait before killing.

    Returns:
        list of processes that had to be killed forcefully.
    """
    # TODO: Better checking.
    procs = child_manager.active_children()
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

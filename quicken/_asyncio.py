"""Asyncio utility classes.
"""
import asyncio
import concurrent.futures
from dataclasses import dataclass
import logging
import multiprocessing
import threading
from typing import Dict


logger = logging.getLogger(__name__)


class ProcessFuture(concurrent.futures.Future):
    def __init__(self, process: multiprocessing.Process):
        super().__init__()
        self.process = process


@dataclass
class CompletedProcess:
    exitcode: int


class ProcessExecutor(concurrent.futures.Executor):
    """Execute each submitted function in its own process.

    This allows arbitrary functions to be provided to the executor, not limited
    to what is pickle-able.

    Instead of the result of the function we return a CompletedProcess.
    """
    def __init__(self):
        # Protects state change and fd/future containers.
        self._lock = threading.Lock()
        # multiprocess.Process should be safe to use in the context of an
        # event loop since it calls `os._exit` instead of returning control
        # to the event loop in the child process.
        # fork is required because both forkserver and spawn prohibit running
        # nested functions.
        self._ctx = multiprocessing.get_context('fork')
        # Pipe used internally for signaling updates to the monitoring thread.
        self._recv_pipe, self._send_pipe = multiprocessing.Pipe(duplex=False)
        self._fds = {self._recv_pipe}
        self._futures: Dict[int, ProcessFuture] = {}
        self._shutdown = False
        self._shutdown_condition = threading.Condition(self._lock)
        # Monitor thread.
        self._thread = threading.Thread(
            target=self._monitor, name='process-executor-monitor', daemon=True)
        self._started = False

    def submit(self, fn, *args, **kwargs):
        with self._lock:
            if not self._started:
                # Start thread lazily to avoid deadlock on acquiring self._lock
                # when starting before fork. See
                # https://bugs.python.org/issue6721.
                self._thread.start()
                self._started = True
            elif self._shutdown:
                raise RuntimeError('Executor already shut down')
        p = self._ctx.Process(
            target=fn, args=args, kwargs=kwargs, name='process-executor-worker')
        future = ProcessFuture(p)
        assert future.set_running_or_notify_cancel(), \
            'set_running_or_notify_cancel must be True'
        p.start()

        with self._lock:
            self._fds.add(p.sentinel)
            self._futures[p.sentinel] = future
        self._send_pipe.send(0)
        return future

    def shutdown(self, wait=True):
        with self._lock:
            self._shutdown = True
            self._send_pipe.send(0)
            if wait:
                self._shutdown_condition.wait()

    def active_count(self):
        with self._lock:
            return len(self._futures)

    def _monitor(self):
        """Monitor all child processes as
        """
        while True:
            with self._lock:
                if self._shutdown and not self._futures:
                    self._shutdown_condition.notify()
                    break
            self._monitor_loop()

    def _monitor_loop(self):
        with self._lock:
            l = list(self._fds)
        logger.debug(f'fds: {l}')
        ready = multiprocessing.connection.wait(l)
        logger.debug(f'Ready: {ready}')
        assert ready, 'Must have value when wait is called without timeout'
        for fd in ready:
            if fd == self._recv_pipe:
                logger.debug('is self._recv_pipe')
                self._recv_pipe.recv()
            else:
                logger.debug('is process')
                with self._lock:
                    self._fds.remove(fd)
                    future = self._futures.pop(fd)
                result = CompletedProcess(future.process.exitcode)
                logger.debug('setting result')
                future.set_result(result)
                logger.debug('after setting result')

from ._debug import log_calls
log_calls(ProcessExecutor)

class DeadlineTimer:
    """Timer that can handle waits > 1 day, since Python < 3.7.1 does not.
    """
    MAX_DURATION = 86400

    def __init__(self, callback, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._callback = callback
        self._time_remaining = 0
        self._handle: asyncio.TimerHandle = None

    def cancel(self):
        if self._handle:
            self._handle.cancel()
            self._handle = None

    def expires_from_now(self, seconds):
        if seconds < 0:
            raise ValueError('seconds must be positive')
        self.cancel()
        wait = seconds % self.MAX_DURATION
        self._time_remaining = max(seconds - wait, 0)
        self._handle = self._loop.call_later(wait, self._handle_expiration)

    def expires_at(self, seconds):
        self.expires_from_now(seconds - self._loop.time())

    def _handle_expiration(self):
        self._handle = None
        if not self._time_remaining:
            self._callback()
        else:
            self.expires_from_now(self._time_remaining)

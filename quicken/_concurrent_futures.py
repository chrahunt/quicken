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
        # The 'fork' context is required because both forkserver and spawn
        # prohibit running nested functions. Nested functions are convenient for
        # testing and most flexible for users.
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
            # Thread is not active.
            if not self._started:
                return
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
                    logger.debug('Signalling shutdown')
                    self._shutdown_condition.notify()
                    break
            self._monitor_loop()

    def _monitor_loop(self):
        with self._lock:
            watched_fds = list(self._fds)
        logger.debug('Waiting for: %s', watched_fds)
        ready = multiprocessing.connection.wait(watched_fds)
        logger.debug('Ready: %s', ready)
        assert ready, 'Must have value when wait is called without timeout'
        for fd in ready:
            if fd == self._recv_pipe:
                self._recv_pipe.recv()
            else:
                with self._lock:
                    self._fds.remove(fd)
                    future = self._futures.pop(fd)
                result = CompletedProcess(future.process.exitcode)
                future.set_result(result)

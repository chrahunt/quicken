import asyncio
import concurrent.futures
from dataclasses import dataclass
from functools import partial
import logging
import multiprocessing
from typing import Dict


logger = logging.getLogger(__name__)


class ProcessFuture(concurrent.futures.Future):
    def __init__(self, process: multiprocessing.Process):
        super().__init__()
        self.process = process


@dataclass
class CompletedProcess:
    exitcode: int


class AsyncProcessExecutor(concurrent.futures.Executor):
    """Execute each submitted function in its own process.

    This allows arbitrary functions to be provided to the executor, not limited
    to what is pickle-able.

    Instead of the result of the function we return a CompletedProcess.
    """
    def __init__(self, loop=None):
        if loop is None:
            loop = asyncio.get_event_loop()
        self._loop = loop
        # multiprocess.Process should be safe to use in the context of an
        # event loop since it calls `os._exit` instead of returning control
        # to the event loop in the child process.
        # The 'fork' context is required because both forkserver and spawn
        # prohibit running nested functions. Nested functions are convenient for
        # testing and most flexible for users.
        self._ctx = multiprocessing.get_context('fork')
        self._fds = set()
        self._futures: Dict[int, ProcessFuture] = {}
        self._shutdown = False
        self._process_cv = asyncio.Condition()

    def submit(self, fn, *args, **kwargs):
        if self._shutdown:
            raise RuntimeError('Executor already shut down')
        p = self._ctx.Process(
            target=fn, args=args, kwargs=kwargs, name='process-executor-worker')
        future = ProcessFuture(p)
        assert future.set_running_or_notify_cancel(), \
            'set_running_or_notify_cancel must be True'
        p.start()
        self._loop.add_reader(p.sentinel, partial(self._handle_process_stop, p.sentinel))
        self._futures[p.sentinel] = future
        return future

    def shutdown(self, wait=True):
        # TODO: Support running from within the event loop itself.
        self._loop.run_until_complete(self.async_shutdown())

    async def async_shutdown(self):
        async with self._process_cv:
            self._process_cv.wait_for(lambda: not len(self._futures))

    def active_count(self):
        return len(self._futures)

    def _handle_process_stop(self, fd):
        self._loop.remove_reader(fd)
        future = self._futures.pop(fd)
        result = CompletedProcess(future.process.exitcode)
        future.set_result(result)

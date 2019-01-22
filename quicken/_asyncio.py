"""Asyncio utility classes.
"""
import asyncio
import logging
import multiprocessing
import os
import signal


logger = logging.getLogger(__name__)


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


class AsyncProcess:
    """asyncio wrapper for multiprocessing.Process.

    Not thread-safe.
    """
    def __init__(self, target, args=(), kwargs=None, loop=None):
        self._target = target
        self._args = args
        if kwargs is None:
            kwargs = {}
        self._kwargs = kwargs
        if loop is None:
            loop = asyncio.get_running_loop()
        self._loop = loop
        self._process: multiprocessing.Process = None
        self._stop_cv = asyncio.Condition()
        self._stopped = False

    def start(self):
        assert self._process is None, 'Process was already started'
        self._process = multiprocessing.get_context('fork').Process(
            target=self._target, args=self._args, kwargs=self._kwargs)
        self._process.start()
        # Clean up to avoid lingering references.
        self._target = None
        self._args = None
        self._kwargs = None
        self._loop.add_reader(self._process.sentinel, self._handle_process_stop)

    def send_signal(self, sig):
        assert self._process is not None, 'Process must be started'
        os.kill(self._process.pid, sig)

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)

    @property
    def pid(self):
        return self._process.pid

    @property
    def exitcode(self):
        return self._process.exitcode

    async def wait(self):
        async with self._stop_cv:
            await self._stop_cv.wait_for(lambda: self._stopped)
            return self.exitcode

    def _handle_process_stop(self):
        self._loop.remove_reader(self._process.sentinel)
        self._process.join()

        async def notify():
            async with self._stop_cv:
                self._stopped = True
                self._stop_cv.notify_all()

        self._loop.create_task(notify())

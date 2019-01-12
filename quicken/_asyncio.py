"""Asyncio utility classes.
"""
import asyncio
import logging


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

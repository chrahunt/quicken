"""Wrappers around multiprocessing for use with asyncio.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import socket

from contextlib import contextmanager

from ._imports import asyncio
from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import Any


logger = logging.getLogger(__name__)


class ConnectionClose(Exception):
    pass


class AsyncConnectionAdapter:
    """Wraps multiprocessing.Connection.

    The underlying socket is still blocking, but this works better in our async
    server.

    Not thread-safe.
    """

    connection: multiprocessing.connection.Connection
    """The underlying connection object."""

    def __init__(self, conn: multiprocessing.connection.Connection, loop=None):
        if not loop:
            loop = asyncio.get_running_loop()
        self._loop = loop
        self.connection = conn
        self._fd = self.connection.fileno()
        self._connected = True
        self._disconnect_cv = asyncio.Condition(loop=self._loop)
        self._attached = False
        self._read_queue = asyncio.Queue(loop=self._loop)
        self._write_queue = asyncio.Queue(loop=self._loop)
        self._attach_to_event_loop()

    async def send(self, obj: Any):
        """
        Send provided object over Connection.
        Args:
            obj: picklable object
        """
        await self._write_queue.put(obj)

    async def recv(self) -> Any:
        """
        Returns:
            message received from peer

        Raises:
            ConnectionClose on connection close
        """
        item = await self._read_queue.get()
        if isinstance(item, ConnectionClose):
            raise item
        return item

    async def closed(self):
        """Returns when the connection is closed.
        """
        if self._connected:
            async with self._disconnect_cv:
                await self._disconnect_cv.wait()

    def _attach_to_event_loop(self):
        assert not self._attached, 'Must not be attached to attach'
        self._attached = True
        self._loop.add_reader(self._fd, self._handle_readable)
        self._loop.add_writer(self._fd, self._handle_writable)

    def _detach_from_event_loop(self):
        assert self._attached, 'Must be attached to detach'
        self._attached = False
        self._loop.remove_reader(self._fd)
        self._loop.remove_writer(self._fd)

    def _handle_readable(self):
        try:
            self._read_queue.put_nowait(self.connection.recv())
        except EOFError:
            self._handle_disconnect()

    def _handle_disconnect(self):
        async def signal_disconnect():
            async with self._disconnect_cv:
                self._disconnect_cv.notify_all()
        self._detach_from_event_loop()
        # XXX: Should include __cause__ or context.
        self._read_queue.put_nowait(ConnectionClose())
        self._connected = False
        self._loop.create_task(signal_disconnect())

    def _handle_writable(self):
        async def handle():
            msg = await self._write_queue.get()
            try:
                self.connection.send(msg)
            except BrokenPipeError:
                self._handle_disconnect()

            if self._attached:
                self._loop.add_writer(self._fd, self._handle_writable)

        self._loop.remove_writer(self._fd)
        self._loop.create_task(handle())


@contextmanager
def intercepted_sockets():
    """Capture any socket objects created in an associated with statement.
    """
    sockets = []
    _socket_new = socket.socket.__new__

    def socket_new(*args, **kwargs):
        sock = _socket_new(*args, **kwargs)
        sockets.append(sock)
        return sock
    socket.socket.__new__ = socket_new
    try:
        yield sockets
    finally:
        socket.socket.__new__ = _socket_new


class ListenerStopped(Exception):
    pass


class AsyncListener:
    """Async multiprocessing.connection.Listener.

    Not thread-safe.
    """
    def __init__(self, address, loop=None):
        if not loop:
            loop = asyncio.get_running_loop()
        self._loop = loop

        self._backlog = asyncio.Queue(loop=self._loop)
        with intercepted_sockets() as sockets:
            self._listener = multiprocessing.connection.Listener(address)
        assert len(sockets) == 1, 'Only one socket should have been created'
        self._socket = sockets[0]
        logger.debug('Started listener (%d)', self._socket.fileno())
        self._accepting = True
        self._attached = False
        self._shutdown = False

        self._attach_to_event_loop()

    async def accept(self) -> AsyncConnectionAdapter:
        """Accept the next incoming connection.

        Raises:
            ListenerStopped
        """
        connection = await self._backlog.get()
        if isinstance(connection, ListenerStopped):
            raise connection
        return AsyncConnectionAdapter(connection, loop=self._loop)

    async def close(self):
        """Close the listener, waiting for the socket to be closed.

        This close does not prevent racing with an incoming client -
        consumers should use higher-level locking if required.
        """
        logger.debug('AsyncListener.close()')
        try:
            if not self._accepting:
                self._shutdown = True
                return
            if self._shutdown:
                return
            self._shutdown = True
            # Detach so no more events are received and any pending events
            # cancelled.
            self._detach_from_event_loop()
            logger.debug('Closing Listener')
            # Removes socket file (if it exists) and calls socket.close().
            self._listener.close()
            # Signal to consumers that the queue is drained.
            self._backlog.put_nowait(ListenerStopped())
        except FileNotFoundError:
            # No problem if the socket file has already been removed.
            pass

    def _attach_to_event_loop(self):
        assert not self._attached, 'Must not be attached to attach'
        self._attached = True
        self._loop.add_reader(self._socket.fileno(), self._handle_readable)

    def _detach_from_event_loop(self):
        assert self._attached, 'Must be attached to detach'
        self._attached = False
        self._loop.remove_reader(self._socket.fileno())

    def _handle_readable(self):
        try:
            self._backlog.put_nowait(self._listener.accept())
        except multiprocessing.AuthenticationError:
            logger.warning('Authentication error')
        except OSError as e:
            # Listener raises its own error with no errno.
            if e.errno is not None:
                raise
            # EINVAL raised when socket is closed, expected condition.
            #if e.errno != errno.EINVAL:
            #    raise
            self._handle_disconnect()

    def _handle_disconnect(self):
        self._detach_from_event_loop()
        self._accepting = False


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

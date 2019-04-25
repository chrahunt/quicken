"""Transferring files over connections.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import socket
import sys

from contextlib import contextmanager
from io import TextIOWrapper
from multiprocessing.connection import wait
from multiprocessing.reduction import register
from typing import Any, TextIO


try:
    from multiprocessing.reduction import DupFd
except ImportError:
    # Can happen on Windows
    DupFd = None


logger = logging.getLogger(__name__)


def run_in_process(
        target, name=None, args=(), kwargs=None, allow_detach=False,
        timeout=None):
    """Run provided target in a multiprocessing.Process.

    This function does not require that the `target` and arguments
    are picklable. Only the return value of `target` must be.

    Args:
        target: same as multiprocessing.Process
        name: same as multiprocessing.Process
        args: same as multiprocessing.Process
        kwargs: same as multiprocessing.Process
        allow_detach: passes a callback as the first argument to the function
            that, when invoked, detaches from the parent by forking.
        timeout: seconds after which processing will be aborted and
            the child process killed

    Returns:
        The return value of `target`

    Raises:
        *: Any exception raised by `target`.
        TimeoutError: If a timeout occurs.
    """
    if not kwargs:
        kwargs = {}

    def launcher():
        # multiprocessing doesn't offer a good way to detach from the parent
        # process, allowing the child to exist without being cleaned up at
        # parent close. So given
        #
        # 1. parent process (which invoked run_in_process)
        # 2. runner process (executing target function)
        #
        # we fork (2), creating (3) then continue executing in (3) and forcibly
        # exit (2).
        #
        # The downside of this approach is that any exceptions from the
        # process after detaching will not be propagated to the caller
        # (and Windows incompatibility).
        def detach(result=None):
            # Indicate no exception.
            child_pipe.send(False)
            child_pipe.send(result)
            pid = os.fork()
            if pid:
                # Ensure we don't return to caller within the subprocess.
                os._exit(0)

        new_args = list(args)
        if allow_detach:
            new_args.insert(0, detach)
        try:
            result = target(*new_args, **kwargs)
        except:
            child_pipe.send(True)
            from tblib import pickling_support
            pickling_support.install()
            child_pipe.send(sys.exc_info())
            # Wait for signal from parent process to avoid exit/read race
            # condition.
            child_pipe.recv()
            # We don't really want the exception traced by multiprocessing
            # so exit like Python would.
            sys.exit(1)
        else:
            child_pipe.send(False)
            child_pipe.send(result)
            child_pipe.recv()

    ctx = multiprocessing.get_context('fork')

    child_pipe, parent_pipe = ctx.Pipe()
    p = ctx.Process(target=launcher, name=name)
    p.start()

    ready = wait([p.sentinel, parent_pipe], timeout=timeout)

    # Timeout
    if not ready:
        p.kill()
        raise TimeoutError('Timeout running function.')

    exc = None
    result = None
    if parent_pipe in ready:
        error = parent_pipe.recv()
        if error:
            from tblib import pickling_support
            pickling_support.install()
            _, exception, tb = parent_pipe.recv()
            exc = exception.with_traceback(tb)
        else:
            result = parent_pipe.recv()

    if p.sentinel in ready:
        # This can happen if the child process closes file descriptors, but we
        # do not handle it.
        assert p.exitcode is not None, 'Exit code must exist'
        if p.exitcode:
            if not exc:
                exc = RuntimeError(
                    f'Process died with return code {p.exitcode}')

    else:
        # Indicate OK to continue.
        parent_pipe.send(True)
        p.join()

    if exc:
        raise exc
    return result


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


def reduce_textio(obj: TextIO):
    # Picklable object that contains a callback id to be used by the
    # receiving process.
    if obj.readable() == obj.writable():
        raise ValueError(
            'TextIO object must be either readable or writable, but not both.')
    df = DupFd(obj.fileno())
    return rebuild_textio, (df, obj.readable(), obj.writable())


def rebuild_textio(df: DupFd, readable: bool, _writable: bool) -> TextIO:
    fd = df.detach()
    flags = 'r' if readable else 'w'
    return open(fd, flags)


register(TextIOWrapper, reduce_textio)

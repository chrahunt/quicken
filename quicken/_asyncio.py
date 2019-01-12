"""Asyncio utility classes.
"""
import asyncio
import concurrent.futures
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import logging
import multiprocessing
import multiprocessing.connection
import socket
import threading
from typing import Any, Dict

from ._debug import log_calls


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


log_calls(DeadlineTimer)


class ConnectionClose(Exception):
    pass


class AsyncConnectionAdapter:
    """Wraps multiprocessing.Connection.

    The underlying socket is still blocking, but this works better in our async
    server.

    Not thread-safe.
    """
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
            self.connection.send(msg)
            if self._attached:
                self._loop.add_writer(self._fd, self._handle_writable)

        self._loop.remove_writer(self._fd)
        self._loop.create_task(handle())


log_calls(AsyncConnectionAdapter)


@contextmanager
def intercepted_sockets():
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
        logger.debug('Listener (%d)', self._socket.fileno())
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


log_calls(AsyncListener)

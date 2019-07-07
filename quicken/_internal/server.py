"""
Command execution server process.

| Server process |   | forkserver |

client    server

run -------
client -> server

To allow use of any callable in the server we override the forkserver
implementation and do not
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import multiprocessing
import os
import signal
import socket
import sys
import time
import traceback

from abc import ABC, abstractmethod
from contextlib import ExitStack

from ._asyncio import DeadlineTimer
from ._imports import asyncio
from ._logging import ContextLogger
from ._multiprocessing import run_in_process
from ._multiprocessing_asyncio import (
    AsyncConnectionAdapter,
    AsyncListener,
    AsyncProcess,
    ConnectionClose,
    ListenerStopped,
)
from ._typing import MYPY_CHECK_RUNNING
from ._signal import settable_signals
from .constants import socket_name, stop_socket_name, server_state_name
from .protocol import ProcessState, Request, RequestTypes, Response
from .xdg import RuntimeDir
from .. import __version__

if MYPY_CHECK_RUNNING:
    from typing import Any, Dict, Optional

    from ._types import NoneFunction


logger = ContextLogger(logging.getLogger(__name__), prefix="server_")


def run(
    socket_handler,
    runtime_dir: RuntimeDir,
    server_idle_timeout: Optional[float],
    user_data: Optional[Any],
):
    """Start the server in the background.

    The function returns only when the server has successfully started.

    Args:
        socket_handler: function invoked on each request
        runtime_dir: directory for holding socket/pid file and used as the
            working directory for the server
        server_idle_timeout: timeout after which server will shutdown if no
            active requests
        user_data: JSON-serializable object put into server metadata file
    Raises:
        If there are any issues starting the server errors are re-raised.
    """
    logger.debug("Starting server launcher")

    daemon_options = {
        # This ensures that relative files are created in the context of the
        # actual runtime dir and not at the path that happens to exist at the
        # time.
        "working_directory": runtime_dir.fileno()
    }

    target = functools.partial(
        _run_server, socket_handler, server_idle_timeout, user_data
    )
    return run_in_process(daemonize, args=(target, daemon_options), allow_detach=True)


def daemonize(detach, target, daemon_options: Dict):
    os.chdir(daemon_options["working_directory"])

    # Make the process a process leader - signals sent to the parent group
    # will no longer propagate by default.
    os.setsid()

    # Secure umask by default.
    os.umask(0o077)

    # Reset signal handlers.
    # We omit SIGPIPE so the default Python signal handler correctly translates
    # it into an exception on socket write.
    for signum in settable_signals - {signal.SIGPIPE}:
        signal.signal(signum, signal.SIG_DFL)

    # Reset signal disposition.
    signal.pthread_sigmask(signal.SIG_SETMASK, set())

    # Redirect streams.
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())

    target(detach)


def _run_server(
    callback, server_idle_timeout: Optional[float], user_data, done
) -> None:
    """Server that provides sockets to `callback`.

    Method must be invoked when cwd is suitable for secure creation of files.

    Args:
        callback: the callback function invoked on each request
        server_idle_timeout: timeout after which the server will stop
            automatically
        done: callback function invoked after setup and before we start handling
            requests
    """
    logger.debug("_run_server()")

    loop = asyncio.new_event_loop()

    def print_exception(_loop, context):
        exc = context.get("exception")
        if exc:
            formatted_exc = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        else:
            formatted_exc = "<no exception>"
        logger.error("Error in event loop: %r\n%s", context, formatted_exc)

    loop.set_exception_handler(print_exception)

    handler = ProcessConnectionHandler(callback, {}, loop=loop)

    def finish_loop():
        logger.debug("Stopping loop")
        loop.stop()
        tasks = asyncio.all_tasks(loop)
        logger.debug("Number of pending tasks: %d", len(tasks))
        loop.run_until_complete(asyncio.gather(*tasks))
        logger.debug("Finished pending tasks")

    # socket_name is relative and we must already have cwd set to the
    # runtime_dir.
    server = Server(
        socket_name,
        stop_socket_name,
        handler,
        finish_loop,
        server_idle_timeout,
        loop=loop,
    )

    def handle_sigterm():
        logger.debug("Received SIGTERM")
        loop.create_task(server.stop())

    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)

    done()

    # For logging.
    multiprocessing.current_process().name = "server"

    # For server state info.
    pid = os.getpid()
    server_state = {
        "create_time": time.time(),
        "lib_version": __version__,
        "idle_timeout": server_idle_timeout,
        "pid": pid,
        "user_data": user_data,
        "groups": os.getgroups(),
        "gid": os.getgid(),
    }

    with open(server_state_name, "w", encoding="utf-8") as f:
        json.dump(server_state, f)

    logger.debug("Starting server")
    server.serve()

    loop.run_forever()
    logger.debug("Server finished.")


class ConnectionHandler(ABC):
    @abstractmethod
    async def handle_connection(self, connection: AsyncConnectionAdapter):
        pass

    @abstractmethod
    async def handle_shutdown(self):
        pass


connection_id_var = contextvars.ContextVar("server_connection_id")


class ProcessConnectionHandler(ConnectionHandler):
    def __init__(self, callback, context: Dict[str, str], loop=None):
        """
        Args:
            callback: function to be executed in child process
            context: server execution context, pretty much a user info object
        """
        if not loop:
            loop = asyncio.get_event_loop()
        self._loop = loop

        self._callback = callback
        self._context = context
        self._start_time = time.time()
        self._pid = os.getpid()
        self._connection_finish_cv = asyncio.Condition(loop=self._loop)
        self._num_active_connections = 0

    async def handle_connection(self, connection: AsyncConnectionAdapter):
        self._num_active_connections += 1
        connection_id_var.set(connection.connection.fileno())
        logger.debug("Handling connection")
        try:
            await self._handle_connection(connection)
        except Exception:
            logger.exception("Unexpected exception handling connection")
            raise
        finally:
            logger.debug("Done with connection")
            self._num_active_connections -= 1
            async with self._connection_finish_cv:
                self._connection_finish_cv.notify_all()

    async def _handle_connection(self, connection: AsyncConnectionAdapter):
        # noinspection PyTypeChecker
        process: AsyncProcess = None
        # noinspection PyTypeChecker
        process_task: asyncio.Task = None
        queue = asyncio.Queue()
        # Have 2 asynchronous tasks running in a loop:
        # - accept_request
        # - handle_request
        # accept_request waits on connection receive or error.
        # on receipt, accept_request pushes to queue
        # on error, accept_request
        # handle_request always waits on the queue for requests and then handles
        #  them as it is able

        async def handle_request():
            """Wait for and handle a single request."""
            nonlocal process, process_task
            logger.debug("Waiting for request")
            request = await queue.get()

            if request.name == RequestTypes.run_process:
                assert process is None, "Process must not have been started"
                process_state = request.contents
                process = self._start_callback(process_state)
                process_task = asyncio.create_task(process.wait())
                pid = process.pid
                logger.debug("Running process in handler: %d", pid)
                await connection.send(Response(pid))

            elif request.name == RequestTypes.wait_process_done:
                assert process is not None, "Process must have been started"
                logger.debug("Waiting for process to exit")
                # We don't want the process.wait() task to be cancelled in case
                # our connection gets broken.
                exitcode = await asyncio.shield(process_task)
                logger.debug("Result: %d", exitcode)
                await connection.send(Response(exitcode))

            return True

        async def accept_request():
            try:
                request: Request = await connection.recv()
            except ConnectionClose:
                logger.debug("Connection closed")
            except ConnectionResetError:
                logger.debug("Connection reset")
            else:
                # We dispatch asynchronously so we can always notice connection
                # reset quickly.
                queue.put_nowait(request)
                return True

            # This occurs when we have disconnected from the client so cancel
            # any pending responses and kill the child process.
            if process:
                logger.debug("Killing child process")
                try:
                    process.kill()
                except ProcessLookupError:
                    # No problem, process already exited.
                    pass

            logger.debug("Cancelling request handler")
            request_handler.cancel()

        async def loop(coro):
            while True:
                if not await coro():
                    break

        request_acceptor = asyncio.create_task(loop(accept_request))
        request_handler = asyncio.create_task(loop(handle_request))
        all_tasks = asyncio.gather(request_acceptor, request_handler)

        try:
            await all_tasks
        except asyncio.CancelledError:
            pass
        finally:
            logger.debug("Task cancelled or exception")
            all_tasks.cancel()

        if process_task:
            logger.debug("Waiting for child process to exit")
            logger.debug("Process task: %s", process_task)
            await process_task

    def _start_callback(self, process_state) -> AsyncProcess:
        def setup_child():
            # XXX: Should close open fds (except the one for the sentinel).
            ProcessState.apply_to_current_process(process_state)
            try:
                sys.exit(self._callback())
            except SystemExit as e:
                # multiprocessing sets exitcode to 1 if `sys.exit` is called
                # with `None` or no arguments, so we re-map it here.
                # See https://bugs.python.org/issue35727.
                if e.code is None:
                    e.args = (0,)
                    e.code = 0
                raise

        process = AsyncProcess(target=setup_child, loop=self._loop)
        process.start()
        return process

    async def handle_shutdown(self):
        """Shutdown executor"""
        logger.debug("Waiting for all connection handling to be done")
        # Wait for handling of all connections to be done.
        async with self._connection_finish_cv:
            await self._connection_finish_cv.wait_for(
                lambda: not self._num_active_connections
            )


class Server:
    """A multiprocessing.Connection server accepts new connections and dispatches handling of requests to
     the AsyncProcessExecutor.

    Per https://bugs.python.org/issue21998 asyncio is not fork-safe, so we spawn
    an executor prior to the starting of the event loop which has essentially
    the state that existed after the call to the cli factory.

    Not thread-safe.
    """

    def __init__(
        self,
        socket_path,
        stop_socket_path,
        handler: ConnectionHandler,
        on_shutdown: NoneFunction,
        idle_timeout: Optional[int] = None,
        shutdown_ctx=None,
        loop=None,
    ):
        """
        Args:
            socket_path: path to listen for client connections
            stop_socket_path: path to a socket which, when a client connects,
                will cause the server to shut down
            handler: Handler for received connections
            idle_timeout: timeout (in seconds) after which server will shut itself down
                without any work
            shutdown_ctx: Context manager to be entered prior to server shutdown.
            loop
        """
        if not loop:
            loop = asyncio.get_event_loop()
        self._loop = loop
        self._stop_socket_path = stop_socket_path
        self._listener = AsyncListener(socket_path, loop=self._loop)
        self._handler = handler
        self._idle_timeout = idle_timeout
        self._idle_timer = None
        self._shutdown_ctx = shutdown_ctx
        self._num_active_connections = 0
        self._shutting_down = False
        self._shutdown_accept_cv = asyncio.Condition(loop=self._loop)
        self._on_shutdown = on_shutdown

    def serve(self):
        self._loop.create_task(self._serve_stop())
        self._loop.create_task(self._serve_clients())

    async def stop(self):
        """Gracefully stop server, processing all pending connections.
        """
        # Do server shutdown and pending event handling first.
        # Server shutdown should ensure:
        # 1. No accepted connections are unhandled
        # 2. All pending asynchronous functions have returned
        # 3. All cleanup by the handler is done.
        logger.debug("Server.stop()")
        self._shutting_down = True
        with ExitStack() as stack:
            if self._shutdown_ctx:
                stack.enter_context(self._shutdown_ctx)
            # Prevent timeout from occurring while we're shutting down.
            self._clear_idle_timer()
            # Closing the listener ensures there will be no more connections
            # queued.
            logger.debug("Waiting for listener close")
            await self._listener.close()
            logger.debug("Waiting for pending connections")
            async with self._shutdown_accept_cv:
                await self._shutdown_accept_cv.wait()
            logger.debug("Waiting for handler shutdown")
            await self._handler.handle_shutdown()
            logger.debug("Waiting for shutdown callback")
            # Finish everything off.
            self._on_shutdown()

    async def _serve_stop(self):
        sock = self._stop_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self._stop_socket_path)
        sock.listen(1)
        sock.setblocking(False)
        self._loop.add_reader(sock.fileno(), self._on_stop_connect)

    def _on_stop_connect(self):
        self._loop.remove_reader(self._stop_sock.fileno())
        _sock, _address = self._stop_sock.accept()
        # XXX: Should we close the socket?
        self._loop.create_task(self.stop())

    async def _serve_clients(self):
        while True:
            try:
                connection = await self._listener.accept()
            except ListenerStopped:
                if not self._shutting_down:
                    logger.error("Listener has stopped")
                else:
                    async with self._shutdown_accept_cv:
                        self._shutdown_accept_cv.notify()
                return
            else:
                self._handle_connection(connection)

    def _handle_connection(self, connection: AsyncConnectionAdapter):
        self._idle_handle_connect()

        async def wait_closed():
            await connection.closed()
            self._idle_handle_close()

        self._loop.create_task(wait_closed())
        self._loop.create_task(self._handler.handle_connection(connection))

    def _handle_timeout(self):
        self._loop.create_task(self.stop())

    def _set_idle_timer(self):
        if self._shutting_down:
            return
        if self._idle_timeout is None:
            return
        if self._idle_timer is not None:
            return
        self._idle_timer = DeadlineTimer(self._handle_timeout, self._loop)
        self._idle_timer.expires_from_now(self._idle_timeout)

    def _clear_idle_timer(self):
        if self._idle_timeout is None:
            return
        if self._idle_timer is None:
            return
        self._idle_timer.cancel()
        self._idle_timer = None

    def _idle_handle_connect(self):
        self._num_active_connections += 1
        self._clear_idle_timer()

    def _idle_handle_close(self):
        self._num_active_connections -= 1
        if not self._num_active_connections:
            self._set_idle_timer()

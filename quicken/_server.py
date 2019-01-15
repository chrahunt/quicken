"""
Command execution server process.

| Server process |   | forkserver |

client    server

run -------
client -> server

To allow use of any callable in the server we override the forkserver
implementation and do not
"""
from abc import ABC, abstractmethod
import asyncio
from contextlib import ExitStack
import functools
import json
import logging
import logging.config
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import sys
import time
import traceback
from typing import Callable, Dict, Optional

import daemon
import daemon.daemon
import psutil

from .__version__ import __version__
from ._asyncio import DeadlineTimer
from ._concurrent_futures import AsyncProcessExecutor
from ._debug import log_calls
from ._multiprocessing import run_in_process, AsyncConnectionAdapter, \
    AsyncListener, ConnectionClose, ListenerStopped
from ._typing import NoneFunction
from ._constants import socket_name, server_state_name
from ._protocol import ProcessState, Request, RequestTypes, Response, ServerState
from ._xdg import RuntimeDir


logger = logging.getLogger(__name__)


RequestCallbackT = Callable[[socket.socket], None]


def run(
        socket_handler: RequestCallbackT,
        runtime_dir: RuntimeDir,
        server_idle_timeout: Optional[float] = None,
        log_file: Optional[Path] = None):
    """Start the server in the background.

    The function returns only when the server has successfully started.

    Args:
        socket_handler: function invoked on each request
        runtime_dir: directory for holding socket/pid file and used as the
            working directory for the server
        server_idle_timeout: timeout after which server will shutdown if no
            active requests
        log_file: used for server-side logging
    Raises:
        Same as `open` for log file issues
        If there are any issues starting the server errors are re-raised.
    """
    logger.debug('run()')

    daemon_options = {
        # This ensures that relative files are created in the context of the
        # actual runtime dir and not at the path that happens to exist at the
        # time.
        'working_directory': runtime_dir.fileno(),
        # Keep runtime directory open.
        'files_preserve': [runtime_dir.fileno()],
    }

    target = functools.partial(_run_server, socket_handler, server_idle_timeout)
    return run_in_process(
        daemonize, args=(target, daemon_options, log_file), allow_detach=True)


def daemonize(detach, target, daemon_options: Dict, log_file=None):
    def patch_python_daemon():
        # We don't want to close any open files right now since we
        # cannot distinguish between the ones we want to keep and
        # those we do not. For our use case there should not be
        # many opened files anyway.
        # XXX: If we do eventually want to close open files, keep in mind
        #  that it may be slow and there are platform-specific speedups we
        #  can do.
        def patched(exclude=None):
            return
        daemon.daemon.close_all_open_files = patched

    patch_python_daemon()

    def detach_process_context():
        """The default behavior in python-daemon is to let the parent die, but
        that doesn't work for us - the parent becomes the first client and
        should stay alive.
        """
        # Make the process a process leader - signals sent to the parent group
        # will no longer propagate by default.
        os.setsid()

    # If detach_process is unspecified in the constructor then python-daemon
    # attempts to determine its value dynamically. This involves accessing stdin
    # which can fail if it has been overridden (as in unit tests).
    ctx = daemon.DaemonContext(detach_process=False)
    for k, v in daemon_options.items():
        setattr(ctx, k, v)

    # We handle detaching.
    ctx.detach_process = False

    # Secure umask by default.
    ctx.umask = 0o077

    detach_process_context()

    with ctx:
        if log_file:
            _configure_logging(log_file, loglevel='DEBUG')
        target(detach)


def _run_server(
        callback: RequestCallbackT,
        server_idle_timeout: Optional[float], done) -> None:
    """Server server that provides sockets to `callback`.

    Method must be invoked when cwd is suitable for secure creation of files.

    Args:
        callback: the callback function invoked on each request
        server_idle_timeout: timeout after which the server will stop
            automatically
        done: callback function invoked after setup and before we start handling
            requests
    """
    logger.debug('_run_server()')

    loop = asyncio.new_event_loop()
    loop.set_debug(True)

    def print_exception(_loop, context):
        exc = context['exception']
        formatted_exc = ''.join(
            traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            'Error in event loop: %s\n%s', context['message'], formatted_exc)
    loop.set_exception_handler(print_exception)

    handler = ProcessConnectionHandler(callback, {}, loop=loop)

    def finish_loop():
        logger.debug('Stopping loop')
        loop.stop()
        tasks = asyncio.all_tasks(loop)
        logger.debug('Number of pending tasks: %d', len(tasks))
        loop.run_until_complete(asyncio.gather(*tasks))
        logger.debug('Finished pending tasks')

    # socket_name is relative and we must already have cwd set to the
    # runtime_dir.
    server = Server(
        socket_name, handler, finish_loop, server_idle_timeout, loop=loop)

    def shutdown():
        logger.debug('shutdown()')
        loop.create_task(server.stop())

    loop.add_signal_handler(signal.SIGTERM, shutdown)

    done()

    # For logging.
    multiprocessing.current_process().name = 'server'

    # For server state info.
    pid = os.getpid()
    process = psutil.Process(pid)
    server_state = {
        'version': __version__,
        'create_time': process.create_time(),
        'pid': pid,
    }
    Path(server_state_name).write_text(
        json.dumps(server_state), encoding='utf-8')

    loop.create_task(server.serve())

    loop.run_forever()
    logger.debug('Server finished.')


class ConnectionHandler(ABC):
    @abstractmethod
    async def handle_connection(self, connection: AsyncConnectionAdapter):
        pass

    @abstractmethod
    async def handle_shutdown(self):
        pass


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
        self._executor = AsyncProcessExecutor(loop=loop)
        self._connection_finish_cv = asyncio.Condition(loop=self._loop)
        self._num_active_connections = 0

    async def handle_connection(self, connection: AsyncConnectionAdapter):
        self._num_active_connections += 1
        while True:
            try:
                request: Request = await connection.recv()
            except ConnectionClose:
                logger.debug(
                    'Connection closed (%d)', connection.connection.fileno())
                # TODO: Execute run_process in its own concurrent coroutine and
                #  wait for connection close - then propagate this to the other
                #  process.
                break
            if request.name == RequestTypes.get_server_state:
                state = ServerState(self._start_time, self._pid, self._context)
                await connection.send(Response(state))
            elif request.name == RequestTypes.run_process:
                process_state = request.contents
                result = await self._execute_callback(process_state)
                logger.debug('Process result: %s', result)
                await connection.send(Response(result))
        self._num_active_connections -= 1
        async with self._connection_finish_cv:
            self._connection_finish_cv.notify()

    async def _execute_callback(self, process_state) -> int:
        def setup_child():
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
        result = await asyncio.get_running_loop().run_in_executor(
            self._executor, setup_child)
        return result.exitcode

    async def handle_shutdown(self):
        """Shutdown executor"""
        # Waits for all processes to finish.
        await self._executor.async_shutdown()
        # Wait for handling of all connections to be done.
        async with self._connection_finish_cv:
            await self._connection_finish_cv.wait_for(
                lambda: not self._num_active_connections)


class Server:
    """A multiprocessing.Connection server.Server accepts new connections and dispatches handling of requests to
     the AsyncProcessExecutor.

    Per https://bugs.python.org/issue21998 asyncio is not fork-safe, so we spawn
    an executor prior to the starting of the event loop which has essentially
    the state that existed after the call to the cli factory.

    Not thread-safe.
    """
    def __init__(
            self, socket_path, handler: ConnectionHandler,
            on_shutdown: NoneFunction, idle_timeout: Optional[int] = None,
            shutdown_ctx=None, loop=None):
        """
        Args:
            socket_path:
            handler: Handler for received connections
            idle_timeout:
            shutdown_ctx: Context manager to be entered prior to server shutdown.
            loop:
        """
        if not loop:
            loop = asyncio.get_event_loop()

        self._loop = loop
        self._listener = AsyncListener(socket_path, loop=self._loop)
        self._handler = handler
        self._idle_timeout = idle_timeout
        self._idle_timer = None
        self._shutdown_ctx = shutdown_ctx
        self._num_active_connections = 0
        self._shutting_down = False
        self._shutdown_accept_cv = asyncio.Condition(loop=self._loop)
        self._on_shutdown = on_shutdown

    async def serve(self):
        while True:
            try:
                connection = await self._listener.accept()
            except ListenerStopped:
                if not self._shutting_down:
                    logger.error('Listener has stopped')
                else:
                    async with self._shutdown_accept_cv:
                        self._shutdown_accept_cv.notify()
                return

            logger.debug(
                'Accepted connection (%d)', connection.connection.fileno())
            self._handle_connection(connection)

    async def stop(self):
        """Gracefully stop server, processing all pending connections.
        """
        # Do server shutdown and pending event handling first.
        # Server shutdown should ensure:
        # 1. No accepted connections are unhandled
        # 2. All pending asynchronous functions have returned
        # 3. All cleanup by the handler is done.
        logger.debug('Server.stop()')
        self._shutting_down = True
        with ExitStack() as stack:
            if self._shutdown_ctx:
                stack.enter_context(self._shutdown_ctx)
            # Prevent timeout from occurring while we're shutting down.
            self._clear_idle_timer()
            # Closing the listener ensures there will be no more connections
            # queued.
            logger.debug('Waiting for acceptor')
            await self._listener.close()
            logger.debug('Waiting for pending connections')
            async with self._shutdown_accept_cv:
                await self._shutdown_accept_cv.wait()
            logger.debug('Waiting for handler shutdown')
            await self._handler.handle_shutdown()
            logger.debug('Waiting for shutdown callback')
            # Finish everything off.
            self._on_shutdown()

    def _handle_connection(self, connection: AsyncConnectionAdapter):
        self._idle_handle_connect()

        async def wait_closed():
            await connection.closed()
            self._idle_handle_close()
        self._loop.create_task(wait_closed())
        self._loop.create_task(
            self._handler.handle_connection(connection))

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


log_calls(Server)


def _configure_logging(logfile: Path, loglevel: str) -> None:
    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

    logfile.parent.mkdir(parents=True, exist_ok=True)
    # TODO: Make fully configurable.
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            f'{__name__}-formatter': {
                '()': UTCFormatter,
                'format':
                    '#### [{asctime}][{levelname}][{name}][{process} ({processName})][{thread} ({threadName})]\n    {message}',
                'style': '{',
            }
        },
        'handlers': {
            f'{__name__}-handler': {
                '()': 'logging.handlers.RotatingFileHandler',
                'level': loglevel,
                'filename': str(logfile),
                'encoding': 'utf-8',
                'formatter': f'{__name__}-formatter',
                'maxBytes': 5_000_000,
                'backupCount': 1,
            }
        },
        'loggers': {
            'quicken': {
                'level': loglevel,
                'handlers': [f'{__name__}-handler'],
            },
            'asyncio': {
                'level': loglevel,
                'handlers': [f'{__name__}-handler'],
            },
        },
    })
    logger.info('Logging configured')

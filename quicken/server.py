"""
Command execution server process.

| Server process |   | forkserver |

client    server

run -------
client -> server

To allow use of any callable in the server we override the forkserver
implementation and do not
"""
import asyncio
from contextlib import ExitStack
import functools
import logging
import logging.config
import multiprocessing
from multiprocessing import Pipe, Process
from multiprocessing.connection import Listener, wait
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
import traceback
from typing import Callable, Dict, Optional, NoReturn

import daemon
import daemon.daemon

from ._asyncio import DeadlineTimer, ProcessExecutor
from .constants import socket_name
from .xdg import RuntimeDir


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
        # The owner of the pidfile is the only one that can do any operations
        # within the runtime directory.
        #'pidfile': PidFile(pid_file_name, piddir='.'),
        # Keep runtime directory open.
        'files_preserve': [runtime_dir.fileno()],
    }

    target = functools.partial(_run_server, socket_handler, server_idle_timeout)
    daemon = _Daemon(target, daemon_options, log_file)
    daemon.start()


class TimeoutError(Exception):
    pass


def apply_process(target, name=None, args=(), kwargs=None, timeout=None):
    """Run provided target in a multiprocessing.Process.

    This function does not require that the `target` and arguments
    are picklable. Only the return value of `target` must be.

    Args:
        target: same as multiprocessing.Process
        name: same as multiprocessing.Process
        args: same as multiprocessing.Process
        kwargs: same as multiprocessing.Process
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

    def runner():
        try:
            result = target(*args, **kwargs)
            child_pipe.send(False)
            # XXX: What if an exception happens here?
            child_pipe.send(result)
            child_pipe.recv()
        except:
            child_pipe.send(True)
            from tblib import pickling_support
            pickling_support.install()
            child_pipe.send(sys.exc_info())
            # Wait for signal from parent process to avoid exit/read race
            # condition.
            child_pipe.recv()
            raise

    ctx = multiprocessing.get_context('fork')

    child_pipe, parent_pipe = ctx.Pipe()
    p = ctx.Process(target=runner, name=name)
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


def run_in_daemon(target, daemon_options: Dict, log_file=None):
    def patch_python_daemon():
        # We don't want to close any open files right now since we
        # cannot distinguish between the ones we want to keep and
        # those we do not. For our use case there should not be
        # many opened files anyway.
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
        # will no longer propagate.
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
        target()


class _Daemon:
    """Run a function as a detached daemon.

    One of the primary issues with daemon processes at startup is debugging
    errors, especially when running user code. To that end we want:

    1. Delay return to parent for as long as possible to rule out exceptions
       or early exit in the child.
    2. Make exception handling transparent.
    """
    def __init__(self, target, daemon_options: Dict, log_file=None):
        self._target = target
        self._daemon_options = daemon_options
        self._log_file = log_file
        self._patch()

    @staticmethod
    def _patch():
        """Any required patches.
        """
        close_all_open_files = daemon.daemon.close_all_open_files
        # TODO: Upstream into python-daemon, see open PRs here:
        #  - https://pagure.io/python-daemon/pull-requests
        # TODO: Speed up close by relying on platform-specific speedup
        #  implementations, see:
        #  - AIX: fcntl.F_CLOSEM: https://bugs.python.org/issue1607087
        #  - Solaris: closefrom: https://bugs.python.org/issue1663329
        #  or alternatively don't close any file descriptors at all since
        #  Python by default sets O_CLOEXEC
        def patched_close_all_open_files(exclude=None):
            # For now just bypass so we don't close the fd used for
            # communicating state back in multiprocess.Process.
            return
            # Specific to Linux.
            fd_dir = Path('/proc/self/fd')
            if fd_dir.exists():
                # We shouldn't need much optimization here, as the parent
                # process is not expected to do more than import required
                # libraries.
                for fd in (int(p.stem) for p in fd_dir.iterdir()):
                    if fd in exclude:
                        continue
                    try:
                        os.close(fd)
                    except OSError:
                        # Best effort.
                        pass
            else:
                # XXX: Part of the ctx.open sequence executes `os.closerange`
                #  which can take significantly longer when running under a
                #  debugger or `strace`.
                close_all_open_files(exclude)

        daemon.daemon.close_all_open_files = patched_close_all_open_files

    def start(self, start_timeout=None):
        """Run daemon.

        Raises:
            any exception from the child process
        """
        child_pipe, parent_pipe = Pipe()
        p = Process(target=self._run, args=(child_pipe, self._target), name='server-spawner')
        p.start()
        ready = wait([p.sentinel, parent_pipe], timeout=start_timeout)

        # Timeout
        if not ready:
            p.kill()
            raise RuntimeError('Timeout starting process')

        exc = None
        if parent_pipe in ready:
            error = parent_pipe.recv()
            if error:
                from tblib import pickling_support
                pickling_support.install()
                _, exception, tb = parent_pipe.recv()
                # Re-raise exception.
                exc = exception.with_traceback(tb)

        if p.sentinel in ready:
            if p.exitcode:
                if not exc:
                    exc = RuntimeError(
                        f'Process died with return code {p.exitcode}')
        elif not exc:
            parent_pipe.send(True)
            p.join()

        if exc:
            raise exc

    def _run(self, conn: Connection, callback):
        """

        Args:
            conn:
            callback:

        Returns:

        """
        def done():
            conn.send(False)
            # Fork to detach from multiprocessing child management.
            pid = os.fork()
            if pid:
                pidfile = self._daemon_options['pidfile']
                # TODO: Need higher-level locking to prevent race conditions on
                #  reading/writing the pidfile.
                # Update pidfile with the actual pid.
                fh = pidfile.fh
                fh.seek(0)
                fh.truncate()
                ctx.pidfile.pid = pid
                fh.write(f'{pid}\n')
                fh.flush()
                os.fsync(fh.fileno())
                fh.seek(0)
                # Ensure we don't return to caller.
                os._exit(0)
            # Close sentinel in grandchild, just to keep things clean.
            #os.close(current_process().sentinel)

        try:
            # Pass detach_process to avoid exception within python-daemon when
            # it tries to access stdin in constructor when detach_process is
            # set later.
            ctx = daemon.DaemonContext(detach_process=False)
            for k, v in self._daemon_options.items():
                setattr(ctx, k, v)

            # We handle detaching (multiprocessing.Process + setsid())
            ctx.detach_process = False

            # Secure umask by default.
            ctx.umask = 0o077

            # Keep connection alive.
            ctx.files_preserve.append(conn.fileno())
            # This doesn't work, raising a ValueError('process not started')
            #ctx.files_preserve.append(current_process().sentinel)

            self._detach_process_context()

            with ctx:
                if self._log_file:
                    _configure_logging(self._log_file, loglevel='DEBUG')
                callback(done)
        except SystemExit:
            # Omit processing SystemExit, let it propagate.
            raise
        except:
            conn.send(True)
            from tblib import pickling_support
            pickling_support.install()
            conn.send(sys.exc_info())
            # Wait for signal from parent process to avoid exit/read race
            # condition.
            conn.recv()
            # multiprocessing takes care of exit code mapping.
            raise

    @staticmethod
    def _detach_process_context():
        """Re-implement detach from python-daemon to keep the original parent
        alive.

        This is necessary so that we can transparently invoke the actual command
        when the server comes up.
        """
        # We are already being invoked inside a child process (
        # multiprocessing.Process).
        # setsid to prevent getting killed with the rest of our parent process
        # group.
        os.setsid()
        # We omit the second fork here since it doesn't really matter if the
        # daemon has a controlling terminal and it makes testing easier.


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

    def print_exception(_loop, context):
        exc = context['exception']
        formatted_exc = ''.join(
            traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            'Error in event loop: %s\n%s', context['message'], formatted_exc)
    loop.set_exception_handler(print_exception)

    # socket_name is relative and we must already have cwd set to the
    # runtime_dir.
    server = Server(socket_name, callback, server_idle_timeout, loop=loop)

    def shutdown():
        loop.create_task(server.stop())

    loop.add_signal_handler(signal.SIGTERM, shutdown)

    # Start server before done so it is listening and any connecting clients
    # will not receive ConnectionRefused.
    loop.run_until_complete(server.start())

    done()

    # For logging.
    multiprocessing.current_process().name = 'server'


    server.serve_forever()


class Server:
    """Server accepts new connections and dispatches handling of requests to
     the ProcessExecutor.

    Per https://bugs.python.org/issue21998 asyncio is not fork-safe, so we spawn
    an executor prior to the starting of the event loop which has essentially
    the state that existed after the call to the cli factory.
    """
    def __init__(
            self, socket_path, callback, idle_timeout: Optional[int] = None,
            shutdown_ctx=None, loop=None):
        self._callback = callback
        self._idle_timeout = idle_timeout
        self._executor = ProcessExecutor()
        if not loop:
            loop = asyncio.new_event_loop()
        # We manage our own loop to not pollute the state of spawned children.
        self._loop = loop
        self._loop.set_debug(True)
        self._server = None
        self._shutdown_ctx = shutdown_ctx
        self._active = 0
        self._listener = Listener(socket_path)
        t = threading.Thread(target=self._serve, daemon=True)
        t.start()

    def _serve(self) -> NoReturn:
        while True:
            try:
                conn = self._listener.accept()
            except multiprocessing.AuthenticationError:
                continue
            callback = functools.partial(self._handle_connection, conn)
            # Could technically join thread if this fails due to being called
            # after loop close.
            self._loop.call_soon_threadsafe(callback)

    def _set_idle_timer(self):
        if not self._idle_timeout:
            return
        self._idle_timer = DeadlineTimer(self._timeout, self._loop)
        self._idle_timer.expires_from_now(self._idle_timeout)

    def _clear_idle_timer(self):
        if not self._idle_timeout:
            return
        self._idle_timer.cancel()
        self._idle_timer = None

    async def start(self):
        class Protocol(asyncio.Protocol):
            def connection_made(_, transport: asyncio.Transport):
                self._loop.create_task(self._handle_socket(transport))

        self._server = await self._loop.create_unix_server(
            Protocol, path=self._socket_path)

    async def _handle_connection(self, conn):
        self._active += 1
        self._clear_idle_timer()
        result = await self._loop.run_in_executor(
            self._executor, self._runner, conn)
        # Finish up communication with the process exit code.
        await self._loop.sock_sendall(
            sock, str(result.exitcode).encode('ascii'))
        self._active -= 1
        if not self._active:
            self._set_idle_timer()

    def _runner(self, sock):
        self._callback(sock)

    def _timeout(self):
        if self._executor.active_count():
            self._idle_timer.expires_from_now(self._idle_timeout)
        else:
            self._loop.create_task(self.stop())

    def serve_forever(self):
        if self._server is None:
            self._loop.create_task(self.start())
        # DEBUG
        async def interval():
            while True:
                logger.debug('awake')
                await asyncio.sleep(1)
        self._loop.create_task(interval())
        self._loop.run_forever()

    async def stop(self):
        # Do server shutdown and pending event handling first.
        with ExitStack() as stack:
            # Placeholder for multi-process server admin lock.
            if self._shutdown_ctx:
                stack.enter_context(self._shutdown_ctx)
            os.unlink(socket_name)
            self._server.close()
            await self._server.wait_closed()
        # Wait for any child processes handling client requests.
        self._executor.shutdown()


def _configure_logging(logfile: Path, loglevel: str) -> None:
    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

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

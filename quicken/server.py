from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import signal
import socket
import socketserver
import tempfile
import threading
import time
from typing import Callable, ContextManager, Optional

import daemon
import daemon.daemon
from pid import PidFile

from .constants import pid_file_name, socket_name
from .xdg import RuntimeDir


logger = logging.getLogger(__name__)


RequestCallbackT = Callable[[socket.socket], None]


def _run_server(callback: RequestCallbackT) -> None:
    """Start server that provides requests to `callback`.

    Method must be invoked when cwd is suitable for secure creation of files.

    If no exceptions are raised then this method exits.

    Args:
        callback:
    """
    logger.debug('_run_server()')

    def get_request_handler():
        class RequestHandler(socketserver.BaseRequestHandler):
            def handle(self):
                logger.debug('handle()')
                callback(self.request)
        return RequestHandler

    class ForkingUnixServer(
            socketserver.ForkingMixIn, socketserver.UnixStreamServer):
        def server_close(self):
            logger.info('Graceful shutdown')

    def shutdown():
        if server:
            server.shutdown()

    def signal_handler(sig, _frame):
        logger.debug(f'Received signal: {sig}')
        # Our socketserver is single-threaded, and server.shutdown() blocks, so
        # we need another thread to actually invoke shutdown otherwise we
        # deadlock.
        t = threading.Thread(target=shutdown, daemon=True)
        t.start()

    server = None
    signal.signal(signal.SIGTERM, signal_handler)
    with tempfile.TemporaryDirectory(dir='.') as p:
        # If the server creates the socket file in-place then the waiting
        # client may use it before the server has called `bind` and
        # `listen`. To avoid this race condition we create a temporary file
        # and then rename, which is atomic.
        temp_socket_name = f'{p}/socket'
        server = ForkingUnixServer(temp_socket_name, get_request_handler())
        os.rename(temp_socket_name, socket_name)
    server.serve_forever()


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
                    '#### [{asctime}][{levelname}][{name}]\n    {message}',
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
            __name__: {
                'level': loglevel,
                'handlers': [f'{__name__}-handler'],
            },
        },
    })
    logger.info('Logging configured')


def _detach_process_context() -> int:
    """
    Re-implement detach from python-daemon to keep the original parent alive.
    This is necessary so that we can transparently invoke the actual command
    when the server comes up.

    Returns:
        pid of detached process (0 in child)
    """
    pid = os.fork()
    if pid:
        return pid
    os.setsid()
    # We omit the second fork here since it doesn't really matter if the daemon
    # has a controlling terminal and it makes testing easier.
    return 0


def run(
        callback: RequestCallbackT,
        runtime_dir: RuntimeDir,
        log_file: Optional[Path] = None) -> int:
    """Exposed function for running the daemon.

    Args:
        callback: function invoked on each request with the socket
        runtime_dir: directory for holding socket/pid file and used as the
            working directory for the server.
        log_file: used for server-side logging
    Raises:
        Same as `open` for log file issues
    """
    logger.debug('run()')

    # In general we try to perform any required validation outside the daemon
    # since it is more difficult to debug inside (especially if logging is
    # mis-configured).

    # Configure file logging right before detaching so any errors above would
    # be throw and be traced as expected (in the parent process).
    if log_file:
        _configure_logging(log_file, loglevel='DEBUG')

    # XXX: We may want to validate the callback here, or just leave it to
    #  the caller.
    ctx = daemon.DaemonContext()
    # We handle detaching.
    ctx.detach_process = False

    # This ensures that relative files are created in the context of the actual
    # runtime dir and not at the path that happens to exist at the time.
    ctx.working_directory = runtime_dir.fileno()
    # The owner of the pidfile is the only one that can do any operations within
    # the runtime directory.
    ctx.pidfile = PidFile(pid_file_name, piddir='.')

    ctx.files_preserve = [
        # Keep runtime directory open.
        runtime_dir.fileno(),
    ]

    if log_file:
        # Preserve file opened for logging.
        ctx.files_preserve.append(logger.handlers[0].stream.fileno())

    # Secure umask by default.
    ctx.umask = 0o077

    pid = _detach_process_context()
    if pid:
        return pid

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
        # Specific to Linux.
        fd_dir = Path('/proc/self/fd')
        if fd_dir.exists():
            # We shouldn't need much optimization here, as the parent process is
            # not expected to do more than import required libraries.
            for fd in (int(p.stem) for p in fd_dir.iterdir()):
                if fd in exclude:
                    continue
                try:
                    os.close(fd)
                except OSError:
                    # Best effort.
                    pass
        else:
            # XXX: Part of the ctx.open sequence executes `os.closerange` which
            #  can take significantly longer when running under a debugger or
            #  `strace`.
            close_all_open_files(exclude)

    daemon.daemon.close_all_open_files = patched_close_all_open_files
    try:
        with ctx:
            _run_server(callback)
    except:
        logger.exception('Daemon exception')
        raise
    finally:
        # Prevent returning to caller.
        # noinspection PyProtectedMember
        os._exit(1)


@contextmanager
def make_client() -> ContextManager[socket.socket]:
    """Create socket with attributes appropriate for server communication.
    """
    logger.debug('make_client()')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        yield sock

from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import socket
import socketserver
import time
from typing import Callable, ContextManager, Optional

import daemon
from daemon.pidfile import TimeoutPIDLockFile


logger = logging.getLogger(__name__)


RequestCallbackT = Callable[[socket.socket], None]


def _get_request_handler(callback: RequestCallbackT):
    class RequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            logger.debug('handle()')
            callback(self.request)
    return RequestHandler


class _ForkingUnixServer(
        socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    pass


def _run_server(callback: RequestCallbackT, socket_file: Path):
    logger.debug('run_server()')

    def cleanup():
        if socket_file.exists():
            socket_file.unlink()
    cleanup()
    # TODO: Any way to prevent race conditions:
    #  1. Server not fully initialized by the time the socket file is available.
    #  2. Socket file gets created before server has a chance to create it.
    try:
        server = _ForkingUnixServer(
            str(socket_file), _get_request_handler(callback))
        server.serve_forever()
    finally:
        cleanup()


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
        callback: RequestCallbackT, socket_file: Path,
        log_file: Optional[Path] = None,
        pid_file: Optional[Path] = None) -> int:
    """Exposed function for running the daemon.

    Args:
        callback function invoked on each request with the socket
        socket_file for client/server communication - will be removed if it
            exists
        log_file used for server-side logging
        pid_file passed to daemon
    Raises:
        Same as `open` for log file issues
    """
    logger.debug('run()')

    # In general we try to perform any required validation outside the daemon
    # since it is more difficult to debug inside (especially if logging is
    # mis-configured).

    # XXX: We may want to validate the callback here, or just leave it to
    #  the caller.
    # TODO: Validate socket file/pid file as acceptable - otherwise error gets
    #  raised in daemon which is harder to identify.

    ctx = daemon.DaemonContext()
    # We handle detaching.
    ctx.detach_process = False
    ctx.pidfile = TimeoutPIDLockFile(str(pid_file))

    # Configure file logging right before detaching so any errors above would
    # be throw and be traced as expected (in the parent process).
    if log_file is not None:
        _configure_logging(log_file, loglevel='DEBUG')

    pid = _detach_process_context()
    if pid:
        return pid

    try:
        # XXX: Part of the ctx.open sequence executes `os.closerange` which
        #  can take significantly longer when running under a debugger or
        #  `strace`.
        with ctx:
            _run_server(callback, socket_file)
    except:
        logger.exception('Daemon exception')
        raise
    finally:
        # Prevent returning to caller.
        # noinspection PyProtectedMember
        os._exit(1)


@contextmanager
def make_client() -> ContextManager[socket.socket]:
    logger.debug('make_client()')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        yield sock

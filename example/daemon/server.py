from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import socket
import socketserver
import sys
import time
from typing import Callable, ContextManager

import daemon
from daemon.pidfile import TimeoutPIDLockFile

from .fd import max_pid_len, recv_fds
from .types import NoneFunctionT


logger = logging.getLogger(__name__)


RequestHandlerT = Callable[[socket.socket], None]


def _get_request_handler(callback: RequestHandlerT):
    class ForkingUnixRequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            """Caller is expected to send message, defined as:

                pgid \0 mesg
            """
            logger.debug('handle()')
            callback(self.request)
    return ForkingUnixRequestHandler


class ForkingUnixServer(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    pass


def _run_server(callback: RequestHandlerT, socket_file: Path):
    logger.debug('run_server()')

    def cleanup():
        if socket_file.exists():
            socket_file.unlink()
    cleanup()
    # TODO: Any way to prevent race conditions:
    #  1. Server not fully initialized by the time the socket file is available.
    #  2. Socket file gets created before server has a chance to create it.
    try:
        server = ForkingUnixServer(str(socket_file), _get_request_handler(callback))
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
                'format': '#### [{asctime}][{levelname}][{name}]\n    {message}',
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


def _detach_process_context() -> bool:
    """
    Re-implement detach from python-daemon to keep the original parent alive.
    This is necessary so that we can transparently invoke the actual command
    when the server comes up.

    Returns:
        True if in the detached process.
    """
    def fork(error_message) -> bool:
        """If in the forked process return True.
        """
        try:
            pid = os.fork()
            return not pid
        except OSError as exc:
            error = daemon.daemon.DaemonProcessDetachError(
                f'{error_message}: [{exc.errno:d}] {exc.strerror}')
            raise error

    if not fork(error_message='Failed first fork'):
        return False
    os.setsid()
    if not fork(error_message='Failed second fork'):
        # noinspection PyProtectedMember
        os._exit(0)
    return True


def _get_server_callback(callback: NoneFunctionT) -> RequestHandlerT:
    """Return the actual callback function invoked on request, which does
    negotiation and environment setup by communicating with the client."""
    def server_callback(sock: socket.socket) -> None:
        # TODO: return our pid.
        # stdout, stdin, stderr
        max_fds = 3
        pgid, fds = recv_fds(sock, max_pid_len, max_fds)

        logger.info('Received message %s, num fds: %s', pgid, len(fds))

        if len(fds) != 3:
            logger.error('Received unexpected number of fds.')
            return
        sys.stdin = os.fdopen(fds[0])
        sys.stdout = os.fdopen(fds[1], 'w')
        sys.stderr = os.fdopen(fds[2], 'w')
        # TODO: Read contents and set environment.
        callback()
        sock.sendall(f'{os.getpid()}'.encode('ascii'))

    return server_callback


def run(
        callback: NoneFunctionT, socket_file: Path, logfile: Path,
        pidfile: Path) -> None:
    """Exposed function for running the daemon.

    Args:
        callback
        socket_file for client/server communication - will be removed if it
            exists
        logfile used for server-side logging
        pidfile passed to daemon
    """
    logger.debug('run()')

    # In general we try to perform any required validation outside the daemon
    # since it is more difficult to debug inside (especially if logging is
    # mis-configured).

    # TODO: Validate callback signature.
    # TODO: Validate socket file can be written.

    ctx = daemon.DaemonContext()
    # We handle detaching.
    ctx.detach_process = False
    ctx.pidfile = TimeoutPIDLockFile(str(pidfile))

    # Configure file logging right before detaching so any errors above would
    # be throw and be traced as expected (in the parent process).
    if logfile is not None:
        _configure_logging(logfile, loglevel='DEBUG')

    if not _detach_process_context():
        # In parent.
        return

    try:
        # XXX: Be advised that part of the ctx.open sequence executes
        #  `os.closerange` which takes significantly longer when running under
        #  a debugger or `strace`.
        with ctx:
            _run_server(_get_server_callback(callback), socket_file)
    except:
        logger.exception('Daemon exception')
        raise
    finally:
        # Prevent returning to caller.
        # noinspection PyProtectedMember
        os._exit(1)


@contextmanager
def client(socket_file: str) -> ContextManager[socket.socket]:
    logger.debug('client()')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_file)
        yield sock

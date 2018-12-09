import atexit
from contextlib import contextmanager
import logging
import logging.config
import os
from pathlib import Path
import socket
import socketserver
import sys
import time
from typing import ContextManager

import daemon

from .fd import max_pid_len, recv_fds


logger = logging.getLogger(__name__)


def get_request_handler(callback):
    class ForkingUnixRequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            """Caller is expected to send message, defined as:

                pgid \0 mesg
            """
            logger.debug('handle()')
            callback(self.request)
            return
            # TODO: Ensure that we are in the same session as the caller
            # TODO: os.setpgid to match the caller.
            # TODO: return our pid.
            # stdout, stdin, stderr
            max_fds = 3
            pgid, fds = recv_fds(self.request, max_pid_len, max_fds)

            logger.info('Received message %s, num fds: %s', pgid, len(fds))

            if len(fds) != 3:
                logger.error('Received unexpected number of fds.')
                return
            sys.stdin = os.fdopen(fds[0])
            sys.stdout = os.fdopen(fds[1], 'w')
            sys.stderr = os.fdopen(fds[2], 'w')
            callback()
            self.request.sendall(f'{os.getpid()}'.encode('ascii'))
    return ForkingUnixRequestHandler


class ForkingUnixServer(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    pass


def run_server(callback, socket_file: str):
    logger.debug('run_server()')
    def cleanup():
        if Path(socket_file).exists():
            Path(socket_file).unlink()
    cleanup()
    atexit.register(cleanup)
    server = ForkingUnixServer(socket_file, get_request_handler(callback))
    server.serve_forever()


def configure_logging(logfile, loglevel):
    class UTCFormatter(logging.Formatter):
        converter = time.gmtime

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
                '()': 'logging.FileHandler',
                'level': loglevel,
                'filename': logfile,
                'encoding': 'utf-8',
                'formatter': f'{__name__}-formatter',
            }
        },
        'loggers': {
            __name__: {
                'level': loglevel,
                'handlers': [f'{__name__}-handler'],
            },
        },
    })


def run(callback, socket_file: str, logfile=None, **kwargs):
    logger.debug('run()')
    with daemon.DaemonContext(**kwargs):
        configure_logging(logfile, loglevel='DEBUG')
        logger.debug('DaemonContext()')
        try:
            run_server(callback, socket_file)
        except:
            logger.exception('Daemon exception')
            raise


@contextmanager
def client(socket_file: str) -> ContextManager[socket.socket]:
    logger.debug('client()')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_file)
        yield sock

import functools
import logging
import logging.config
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection, wait
import os
from pathlib import Path
import signal
import socket
import socketserver
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, Optional

import daemon
import daemon.daemon
from pid import PidFile

from .constants import pid_file_name, socket_name
from .xdg import RuntimeDir


logger = logging.getLogger(__name__)


RequestCallbackT = Callable[[socket.socket], None]


def run(
        callback: RequestCallbackT,
        runtime_dir: RuntimeDir,
        log_file: Optional[Path] = None) -> int:
    """Start the server in the background.

    The function returns only when the server has successfully started.

    Args:
        callback: function invoked on each request
        runtime_dir: directory for holding socket/pid file and used as the
            working directory for the server
        log_file: used for server-side logging
    Returns:
        pid of new process
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
        'pidfile': PidFile(pid_file_name, piddir='.'),
        # Keep runtime directory open.
        'files_preserve': [runtime_dir.fileno()],
    }

    target = functools.partial(_run_server, callback)
    daemon = Daemon(target, daemon_options, log_file)
    return daemon.start()


class Daemon:
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

    def start(self, start_timeout=None) -> int:
        """Run daemon.

        Returns:
            pid of new process
        Raises:
            any exception from the child process
        """
        conn1, conn2 = Pipe()
        #p = Process(target=self._dummy)
        p = Process(target=self._run, args=(conn1, self._target))
        p.start()
        ready = wait([p.sentinel, conn2], timeout=start_timeout)
        # Timeout
        if not ready:
            p.kill()
            raise RuntimeError('Timeout starting process')
        if len(ready) == 2:
            # Process died and we have some data available.
            error = conn2.recv()
            if not error:
                # Process died but no error.
                raise RuntimeError(
                    f'Process died with return code {p.exitcode}')
            from tblib import pickling_support
            pickling_support.install()
            _, exception, tb = conn2.recv()
            # Re-raise exception.
            raise exception.with_traceback(tb)
        value = ready[0]
        if value == p.sentinel:
            # Process died but without sending a response.
            raise RuntimeError(
                f'Process died with return code {p.exitcode}')
        else:
            # Process responded.
            error = conn2.recv()
            if not error:
                # No problems, assume process is up and ready.
                return p.pid
            from tblib import pickling_support
            pickling_support.install()
            _, exception, tb = conn2.recv()
            # Re-raise exception but not before killing process.
            p.kill()
            raise exception.with_traceback(tb)

    def _run(self, conn: Connection, callback):
        """

        Args:
            conn:
            callback:

        Returns:

        """
        def done():
            conn.send(False)
        try:
            # Pass detach_process to avoid exception within python-daemon when
            # it tries to access stdin.
            ctx = daemon.DaemonContext(detach_process=False)
            for k, v in self._daemon_options.items():
                setattr(ctx, k, v)

            # We handle detaching (multiprocessing.Process + setsid())
            ctx.detach_process = False

            # Secure umask by default.
            ctx.umask = 0o077

            # Keep connection alive.
            ctx.files_preserve.append(conn.fileno())

            self._detach_process_context()

            with ctx:
                if self._log_file:
                    _configure_logging(self._log_file, loglevel='DEBUG')
                callback(done)
        except:
            conn.send(True)
            from tblib import pickling_support
            pickling_support.install()
            conn.send(sys.exc_info())
            # Wait for signal from parent process to avoid exit/read race
            # condition.
            conn.recv()
            sys.exit(1)

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


def _run_server(callback: RequestCallbackT, done) -> None:
    """Start server that provides requests to `callback`.

    Method must be invoked when cwd is suitable for secure creation of files.

    Args:
        callback: the callback function invoked on each request
        done: callback function invoked after setup and before we start handling
            requests
    """
    logger.debug('_run_server()')

    # XXX: We may want to validate the callback here, or just leave it to
    #  the caller.

    class RequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            logger.debug('handle()')
            callback(self.request)

    class ForkingUnixServer(
            socketserver.ForkingMixIn, socketserver.UnixStreamServer):
        pass

    # If the server creates the socket file in-place then the waiting
    # client may use it before the server has called `bind` and
    # `listen`. To avoid this race condition we create a temporary file
    # and then rename, which is atomic.
    with tempfile.TemporaryDirectory(dir='.') as p:
        temp_socket_name = f'{p}/socket'
        server = ForkingUnixServer(temp_socket_name, RequestHandler)
        os.rename(temp_socket_name, socket_name)

    def shutdown():
        server.shutdown()

    def signal_handler(sig, _frame):
        logger.debug(f'Received signal: {sig}')
        # Our server is single-threaded, and server.shutdown() blocks, so we
        # need another thread to actually invoke shutdown otherwise we deadlock.
        t = threading.Thread(target=shutdown, daemon=True)
        t.start()

    # Gracefully shut down if we get sigterm.
    signal.signal(signal.SIGTERM, signal_handler)

    # Signal OK before handling requests.
    done()

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

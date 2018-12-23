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
        server_idle_timeout: Optional[float] = None,
        log_file: Optional[Path] = None):
    """Start the server in the background.

    The function returns only when the server has successfully started.

    Args:
        callback: function invoked on each request
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
        'pidfile': PidFile(pid_file_name, piddir='.'),
        # Keep runtime directory open.
        'files_preserve': [runtime_dir.fileno()],
    }

    target = functools.partial(_run_server, callback, server_idle_timeout)
    daemon = _Daemon(target, daemon_options, log_file)
    daemon.start()


class _Daemon:
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
        p = Process(target=self._run, args=(child_pipe, self._target))
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
    """Start server that provides requests to `callback`.

    Method must be invoked when cwd is suitable for secure creation of files.

    Args:
        callback: the callback function invoked on each request
        server_idle_timeout: timeout after which the server will stop
            automatically
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
        def __init__(self, *args, **kwargs):
            socketserver.UnixStreamServer.__init__(self, *args, **kwargs)
            self._last_active_time = time.monotonic()

        def service_actions(self):
            """Shutdown server if timeout has been reached.
            """
            # Extend.
            super().service_actions()
            if server_idle_timeout is None:
                return
            now = time.monotonic()
            if self.active_children:
                self._last_active_time = now
            elif now - self._last_active_time >= server_idle_timeout:
                shutdown()

    # If the server creates the socket file in-place then the waiting
    # client may use it before the server has called `bind` and
    # `listen`. To avoid this race condition we create a temporary file
    # and then rename, which is atomic.
    with tempfile.TemporaryDirectory(dir='.') as p:
        temp_socket_name = f'{p}/socket'
        server = ForkingUnixServer(temp_socket_name, RequestHandler)
        os.rename(temp_socket_name, socket_name)

    # The following activities can cause server shutdown:
    # 1. Signal
    # 2. Idle timeout
    def shutdown():
        def inner_shutdown():
            os.unlink(socket_name)
            server.shutdown()
        t = threading.Thread(target=inner_shutdown, daemon=True)
        t.start()

    def signal_handler(sig, _frame):
        logger.debug(f'Received signal: {sig}')
        # Our server is single-threaded, and server.shutdown() blocks, so we
        # need another thread to actually invoke shutdown otherwise we deadlock.
        shutdown()

    # Gracefully shut down if we get sigterm.
    signal.signal(signal.SIGTERM, signal_handler)

    # Signal OK before handling requests.
    done()

    # 10 ms granularity for shutdown/idle timeout
    interval = 0.01
    server.serve_forever(interval)


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

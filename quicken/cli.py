"""CLI wrapper interface for starting/using server process.
"""
# TODO: Move unnecessary imports to cli_impl.py and import only when we're
#  actually running the server.
import logging
from functools import wraps
import os
from multiprocessing.connection import Client
from multiprocessing.reduction import recv_handle, send_handle
from pathlib import Path
import socket
import sys
from typing import Callable, Optional, Union

from fasteners import InterProcessLock
from .constants import socket_name, pid_file_name
from .logging import reset_loggers
from .types import CliFactoryT, NoneFunctionT
from .xdg import chdir, RuntimeDir


logger = logging.getLogger(__name__)


CliFactoryDecoratorT = Callable[[CliFactoryT], NoneFunctionT]
BoolProvider = Callable[[], bool]


def cli_factory(
        name: str,
        runtime_dir_path: Optional[str] = None,
        log_file: Optional[str] = None,
        daemon_start_timeout: float = 5.0,
        daemon_stop_timeout: float = 2.0,
        server_idle_timeout: Optional[float] = None,
        bypass_daemon: Optional[Union[BoolProvider, bool]] = None,
        reload_daemon: Optional[Union[BoolProvider, bool]] = None,
        ) -> CliFactoryDecoratorT:
    """Decorator to mark a function that provides the main script entry point.

    To benefit most from the daemon speedup, you must do required imports
    within the factory function itself and then have the returned function
    itself do a minimal amount of configuration - only those things dependent
    on e.g. environment/cwd.

    If any imported top-level modules make use of environment then they must be
    reconfigured on invocation of the cli, otherwise the environment of future
    clients will not be taken into account.

    Args:
        name: the name used for the socket file.
        runtime_dir_path: the directory used for the socket and pid file. If not
            provided then we fall back to:
            `$XDG_RUNTIME_DIR/quicken-{name}` or `$TMPDIR/quicken-{name}-{uid}`
            or `/tmp/quicken-{name}-{uid}`. If the directory exists it must be
            owned by the current user and have permissions 700.
        log_file: optional log file used by the server, must be absolute path
            since the server is moved to `/`. Default is `~/.daemon-{name}.log`.
        daemon_start_timeout: time in seconds to wait for daemon to start before
            falling back to executing function normally.
        daemon_stop_timeout: time in seconds to wait for daemon to start before
            falling back to executing function normally.
        server_idle_timeout: time in seconds after which the server will shut
            down if no requests are being processed.
        bypass_daemon: if True then run command directly instead of trying to
            use daemon.
        reload_daemon: if True then restart the daemon process before executing
            the function.

    Throws:
        QuickenError: If any directory used by runtime_dir does not have the
            correct permissions.
    """
    logger.debug('test')
    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunctionT:
        @wraps(factory_fn)
        def run_cli() -> Optional[int]:
            """
            Returns:
                Result from function or remote execution, suitable for passing
                to :func:`sys.exit`.
            """
            nonlocal log_file

            if log_file is None:
                log_file = Path(os.environ['HOME']) / f'.daemon-{name}.log'

            if bypass_daemon and bypass_daemon():
                logger.debug('Bypassing daemon')
                return factory_fn()()

            runtime_dir = RuntimeDir(f'quicken-{name}', runtime_dir_path)

            if reload_daemon and reload_daemon():
                logger.debug('Reloading daemon')
                _kill_daemon(runtime_dir, daemon_stop_timeout)

            sock = None
            with CliServerManager(
                    factory_fn, runtime_dir, log_file, server_idle_timeout) as manager:
                try:
                    sock = manager.connect()
                except ConnectionRefusedError:
                    logger.warning(
                        'Failed to connect to server - executing cli directly.')
            if not sock:
                return factory_fn()()
            return _run_client(sock)

        return run_cli

    return inner_cli_factory


class CliClient:
    def __init__(self, callback_factory, server_idle_timeout, server_start_timeout, bypass_server, check_server):
        self._factory = callback_factory
        self._server_idle_timeout = server_idle_timeout
        self._server_start_timeout = server_start_timeout
        self._bypass_server = bypass_server
        self._check_server = check_server

    def __call__(self) -> Optional[int]:
        ...


class CliServerManager:
    """Responsible for connecting to or starting the server.

    To prevent race conditions we lock the runtime directory
    Race conditions are prevented by locking the runtime directory during
    connection and start.
    """
    def __init__(
            self, callback_factory, runtime_dir: RuntimeDir, log_file,
            server_idle_timeout):
        """
        Args:
            callback_factory: function that provides the server request handler
            runtime_dir: runtime dir used for locks/socket
            log_file: server log file
            server_idle_timeout: idle timeout communicated to server if the
                process of connecting results in server start
        """
        self._factory = callback_factory
        self._runtime_dir = runtime_dir
        self._log_file = log_file
        self._idle_timeout = server_idle_timeout
        self._lock = InterProcessLock('admin')

    def connect(self, timeout: Optional[float] = None) -> socket.socket:
        """Attempt to connect to the server. If connection fails then start it.

        Args:
            timeout: seconds to wait for successful connection or startup

        Returns:
            Socket connected to the server
        """
        socket_file = self._runtime_dir.path(socket_name)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Use bound path to prevent running command against socket owned by
            # other user, or one which was created after-the-fact.
            socket_file.pass_to(lambda p: sock.connect(str(p)))
            return sock
        except FileNotFoundError:
            # Server not up, no problem, we'll try to start it.
            logger.debug('Server not up, starting it.')
        except ConnectionRefusedError:
            # Server may have died unexpectedly.
            logger.warning('Could not connect to server, starting it.')
            # Clean up the socket file before proceeding.
            socket_file.unlink()

        self._start_server()

        # Try to connect again, this time we don't catch anything, leave it to
        # the caller.
        socket_file.pass_to(lambda p: sock.connect(str(p)))
        return sock

    def _get_connections(self, sock) -> multiprocessing.connection.Connection:
        """Upgrade a socket connection to a Connection object.
        """
        pass

    def _start_server(self) -> None:
        """Start server as background process.

        The socket for the server has been created by the time this function
        returns.

        This function only returns in the parent, not the background process.
        """
        cli = self._factory()
        # Lazy import so we only take the time to import if we have to start
        # the server.
        from .server import run
        run(
            _get_server_callback(cli), log_file=self._log_file,
            runtime_dir=self._runtime_dir,
            server_idle_timeout=self._idle_timeout)

    def __enter__(self):
        """Enter the server admin lock context.
        """
        with chdir(self._runtime_dir.fileno()):
            # TODO: Ensure that this creates our lock file with 700 since
            #  otherwise it might not be respected.
            self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the server admin lock context.
        """
        self._lock.release()


def _kill_daemon(runtime_dir: RuntimeDir, timeout: float) -> None:
    """Kill existing daemon process (if any).
    """
    # To prevent inadvertently killing an innocent process, we take
    # the following precautions:
    # 1. Assume existing process is daemon only if the pid file is locked.
    # 2. Ensure the pid in the pid file is the same before and after the check.
    # 3. Use psutil.Process which shrinks window for race after second check and
    #    before kill by ensuring the pid has not been reused (saves creation
    #    time).
    # If the pid has changed before/after the check then some other process has
    # come in and replaced the process before us.
    # TODO: Lock this across processes.
    from pid import (
        PidFile, PidFileAlreadyLockedError, PidFileAlreadyRunningError)
    from psutil import NoSuchProcess, Process, TimeoutExpired
    pid_file = runtime_dir.path(pid_file_name)
    # Retrieve the pid of the existing daemon.
    try:
        pid = int(pid_file.read_text(encoding='utf-8'))
    except FileNotFoundError:
        logger.debug(
            'Daemon reload requested but file not found, ignoring.')
        return
    except OSError:
        logger.exception(
            'Daemon reload failed - could not get pid from'
            f' {pid_file}')
        raise

    try:
        process = Process(pid=pid)
    except NoSuchProcess:
        logger.debug(
            f'Daemon reload requested but process with pid {pid}'
            ' does not exist.')
        pid_file.unlink()
        return

    # To ensure this is the same process, see if the pid file is locked.
    with chdir(pid_file.dir):
        try:
            with PidFile(pid_file_name, piddir='.'):
                pid_file.unlink()
                logger.debug(
                    'Daemon reload requested but pid file was not locked')
            return
        except PidFileAlreadyLockedError:
            pass
        except PidFileAlreadyRunningError:
            # Process with the same pid happens to be up, but there's no lock
            # so no problem.
            pid_file.unlink()
            return

    try:
        pid_2 = int(pid_file.read_text(encoding='utf-8'))
    except FileNotFoundError:
        logger.debug(
            'Daemon reload requested but file has already been cleaned up.')
        return
    except OSError:
        logger.exception(
            'Daemon reload failed - could not confirm pid from'
            f' {pid_file}')
        raise

    if pid == pid_2:
        process.terminate()
        try:
            process.wait(timeout)
            # Process should clean up after itself.
        except TimeoutExpired:
            # TODO: Reduce number of edge cases in this condition.
            raise
    else:
        raise RuntimeError(
            'Other daemon process has started when we were checking reload')


def _get_server_callback(callback: NoneFunctionT):
    """Return the actual callback function invoked on request, which does
    negotiation and environment setup by communicating with the client."""
    def server_callback(sock: socket.socket) -> None:
        logger.debug('server_callback()')
        # Expecting stdin/out/err
        stdin_fd = recv_handle(sock)
        stdout_fd = recv_handle(sock)
        stderr_fd = recv_handle(sock)

        stdin = os.fdopen(stdin_fd)
        stdout = os.fdopen(stdout_fd, 'w')
        stderr = os.fdopen(stderr_fd, 'w')
        reset_loggers(stdout, stderr)
        sys.stdin = stdin
        sys.stdout = stdout
        sys.stderr = stderr

        # Length of next message.
        msglen = int(sock.recv(4096))
        sock.sendall(str(os.getpid()).encode('ascii'))
        contents = b''
        while len(contents) != msglen:
            contents += sock.recv(4096)
        state = deserialize_state(contents)
        os.chdir(state['cwd'])
        os.umask(state['umask'])
        os.environ = state['env']
        sys.argv = state['argv']

        sys.exit(callback())

    return server_callback


def _run_client(sock: socket.socket) -> int:
    """Run command client against daemon listening at provided `socket_file`.

    Sends process context and waits for exit code.

    Process context includes:
    - environment
    - cwd
    - command line
    - file descriptors for stdin/out/err

    Args:
        sock: Socket connected to server. Must be a type appropriate for passing
            file descriptors.

    Returns:
        exit code of the process

    Raises:
        ConnectionRefusedError if server is not listening/available.
    """
    logger.debug('_run_client()')
    # XXX: Remote PID is not used on Unix platforms.
    send_handle(sock, sys.stdin.fileno(), 0)
    send_handle(sock, sys.stdout.fileno(), 0)
    send_handle(sock, sys.stderr.fileno(), 0)
    state = serialize_state()
    sock.sendall(str(len(state)).encode('ascii'))
    max_pid_len = len(Path('/proc/sys/kernel/pid_max').read_bytes())
    data = sock.recv(max_pid_len)
    # TODO: Setup signal forwarding.
    logger.debug('Request being handled by %s', data)
    sock.sendall(state)
    # Wait for exit code.
    data = sock.recv(3)
    logger.debug('Handler exited with rc: %s', data)
    return int(data)

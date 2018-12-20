"""CLI wrapper interface for daemonizing process.
"""
# TODO: Move unnecessary imports to cli_impl.py and import only when we're
#  actually running the daemon.
import logging
from functools import wraps
import os
from pathlib import Path
import socket
import sys
from typing import Callable, Optional, Union

from .constants import socket_name, pid_file_name
from .fd import max_pid_len, recv_fds, send_fds
from .logging import reset_loggers
from .protocol import serialize_state, deserialize_state
from .types import CliFactoryT, NoneFunctionT
from .xdg import BoundPath, chdir, RuntimeDir


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
    reconfigured on invocation of the cli, otherwise the environment of the
    client will not be taken into account.

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
        server_idle_timeout: if provided then the server will shut down after
            this long with no active requests (in seconds).
        bypass_daemon: if True then run command directly instead of trying to
            use daemon.
        reload_daemon: if True then restart the daemon process before executing
            the function.

    Throws:
        QuickenError: If any directory used by runtime_dir does not have the
            correct permissions.
    """
    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunctionT:
        @wraps(factory_fn)
        def run_cli() -> Optional[int]:
            """
            Returns:
                Result from function or remote execution, suitable for passing
                to :func:`sys.exit`.
            """
            nonlocal log_file

            if bypass_daemon and bypass_daemon():
                logger.debug('Bypassing daemon')
                return factory_fn()()

            runtime_dir = RuntimeDir(f'quicken-{name}', runtime_dir_path)

            if reload_daemon and reload_daemon():
                logger.debug('Reloading daemon')
                _kill_daemon(runtime_dir, daemon_stop_timeout)

            socket_file = runtime_dir.path(socket_name)

            # If the socket file exists then try to communicate with the server.
            try:
                # Best case - server is already up and we successfully
                # communicate with it,
                return _run_client(socket_file)
            except FileNotFoundError:
                # Server not up, no problem, we'll try to start it.
                logger.debug(
                    'Server not up, starting it.')
            except ConnectionRefusedError:
                # Server may have died unexpectedly.
                logger.warning(
                    'Could not connect to daemon, starting it.')
                # Clean up the socket file before proceeding.
                socket_file.unlink()

            if log_file is None:
                log_file = Path(os.environ['HOME']) / f'.daemon-{name}.log'

            # OK case - server is not up, so we start it.
            # TODO: Wait for daemon death since it can happen quickly and
            #  sends SIGCHLD even though daemon does setsid.
            _start_daemon(
                factory_fn, runtime_dir, log_file, server_idle_timeout)

            try:
                return _run_client(socket_file)
            except ConnectionRefusedError:
                # Bad case - server came up but we could not communicate with
                # it.
                logger.warning(
                    'Failed to communicate with daemon - executing cli'
                    ' directly.')
                return factory_fn()()

        return run_cli

    return inner_cli_factory


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
        # Expecting stdin/out/err
        max_fds = 3
        msglen, fds = recv_fds(sock, max_pid_len, max_fds)
        msglen = int(msglen)

        logger.debug('Received message %s, num fds: %s', msglen, len(fds))

        assert len(fds) == 3, f'Received unexpected number of fds: {len(fds)}'
        stdin = os.fdopen(fds[0])
        stdout = os.fdopen(fds[1], 'w')
        stderr = os.fdopen(fds[2], 'w')
        reset_loggers(stdout, stderr)
        sys.stdin = stdin
        sys.stdout = stdout
        sys.stderr = stderr

        sock.sendall(str(os.getpid()).encode('ascii'))
        contents = b''
        while len(contents) != msglen:
            contents += sock.recv(4096)
        state = deserialize_state(contents)
        os.chdir(state['cwd'])
        os.umask(state['umask'])
        os.environ = state['env']
        sys.argv = state['argv']

        return_code = callback()
        if return_code is None:
            return_code = 0
        sock.sendall(str(return_code).encode('ascii'))

    return server_callback


def _start_daemon(
        factory_fn: CliFactoryT, runtime_dir: RuntimeDir,
        log_file: Path, server_idle_timeout: Optional[float]) -> None:
    """Start daemon process.

    Returns:
        This function only returns in the original process, and exits
        internally in the daemon process.
    """
    logger.debug('_start_daemon()')
    # We don't get any benefit the first time we're starting the daemon, so we
    # get the cli function in the parent process to avoid having to look in
    # the server log for errors.
    # TODO: Execute after detaching from process since this may open required
    #  files for e.g. logging, or specify that such things need to be accounted
    #  for and not done.
    cli = factory_fn()
    from .server import run
    run(
        _get_server_callback(cli), log_file=log_file, runtime_dir=runtime_dir,
        server_idle_timeout=server_idle_timeout)


def _run_client(socket_file: BoundPath) -> int:
    """Run command client against daemon listening at provided `socket_file`.

    Process context includes:
    - environment
    - cwd
    - command line
    - file descriptors for stdin/out/err

    Raises:
        ConnectionRefusedError if server is not listening/available.
    """
    logger.debug('_run_client()')

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        # Use bound path to prevent running command against socket owned by
        # other user, or one which was created after-the-fact.
        socket_file.pass_to(lambda p: sock.connect(str(p)))
        state = serialize_state()
        send_fds(
            sock, f'{len(state)}'.encode('ascii'),
            [sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()])
        logger.debug('Send fds and state length.')
        data = sock.recv(max_pid_len)
        # TODO: Setup signal forwarding.
        logger.debug('Request being handled by %s', data)
        sock.sendall(state)
        # Wait for exit code.
        data = sock.recv(3)
        logger.debug('Handler exited with rc: %s', data)
        return int(data)

"""CLI wrapper interface for daemonizing process.
"""
# TODO: Move unnecessary imports to cli_impl.py and import only when we're
#  actually running the daemon.
import logging
import os
from pathlib import Path
import signal
import socket
import sys
from typing import Callable, Optional, Union

from .server import make_client, RequestCallbackT, run
from .fd import max_pid_len, recv_fds, send_fds
from .logging import reset_loggers
from .protocol import serialize_state, deserialize_state
from .types import CliFactoryT, NoneFunctionT
from .watch import wait_for_create, wait_for_delete


logger = logging.getLogger(__name__)


CliFactoryDecoratorT = Callable[[CliFactoryT], NoneFunctionT]
BoolProvider = Callable[[], bool]


def cli_factory(
        name: str,
        socket_file: Optional[str] = None,
        log_file: Optional[str] = None,
        pid_file: Optional[str] = None,
        daemon_start_timeout: float = 5.0,
        daemon_stop_timeout: float = 2.0,
        bypass_daemon: Optional[Union[BoolProvider, bool]] = None,
        reload_daemon: Optional[Union[BoolProvider, bool]] = None,
        ) -> CliFactoryDecoratorT:
    """Decorator to mark a function that provides the main script entrypoint.

    To benefit most from the daemon speedup, you must do required imports
    within the factory function itself and then have the returned function
    itself do a minimal amount of configuration - only those things dependent
    on e.g. environment/cwd.

    If any imported top-level modules make use of environment then they must be
    reconfigured on invokation of the cli, otherwise the environment of the
    client will not be taken into account.

    Args:
        name the name used for the socket file.
        log_file optional log file used by the server, must be absolute path
          since the server is moved to /. Default is ~/.daemon-{name}.log.
        pid_file optional pid file used by the server, must be absolute path
          since the server is moved to /. Default is ~/.daemon-{name}.pid.
        daemon_timeout time in seconds to wait for daemon to start before
          falling back to executing function normally.
        bypass_daemon if True then run command directly instead of trying to
          use daemon.
        reload_daemon if True then restart the daemon process before executing
          the function.
    """
    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunctionT:
        def run_cli() -> Optional[int]:
            """
            Returns:
                Result from function or remote execution, suitable for passing
                to sys.exit().
            """
            nonlocal log_file, pid_file, socket_file

            if bypass_daemon():
                logger.debug('Bypassing daemon')
                return factory_fn()()

            if pid_file is None:
                pid_file = Path(os.environ['HOME']) / f'.daemon-{name}.pid'

            if reload_daemon():
                logger.debug('Reloading daemon')
                # Retrieve the pid of the existing daemon.
                try:
                    pid = int(pid_file.read_text(encoding='utf-8'))
                except FileNotFoundError:
                    logger.debug(
                        'Daemon reload requested but file not found, ignoring.')
                except OSError:
                    logger.exception(
                        'Daemon reload failed - could not get pid from'
                        f' {pid_file}')
                    raise
                else:
                    # Send sigterm to the daemon.
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        logger.debug(
                            f'Daemon with pid {pid} does not exist, removing'
                            f' pid file {pid_file}.')
                        pid_file.unlink()
                    else:
                        if not wait_for_delete(pid_file, daemon_stop_timeout):
                            raise RuntimeError(
                                f'Daemon reload failed, pid file {pid_file}'
                                 ' still present.')

            if socket_file is None:
                socket_file = Path(os.environ['HOME']) / f'.daemon-sock-{name}'

            # If the socket file exists then try to communicate with the server.
            if socket_file.exists():
                try:
                    # Best case - server is already up and we successfully
                    # communicate with it,
                    return _run_client(socket_file)
                except ConnectionRefusedError:
                    logger.warning(
                        'Could not connect to daemon, starting it.')

            if log_file is None:
                log_file = Path(os.environ['HOME']) / f'.daemon-{name}.log'

            # OK case - server is not up, so we start it.
            _start_daemon(factory_fn, socket_file, log_file, pid_file)

            if not wait_for_create(Path(socket_file), daemon_start_timeout):
                # Bad case - timed out waiting for server startup.
                logger.warning(
                    'Timeout waiting for daemon - executing cli directly.')
                return factory_fn()()

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


def _get_server_callback(callback: NoneFunctionT) -> RequestCallbackT:
    """Return the actual callback function invoked on request, which does
    negotiation and environment setup by communicating with the client."""
    def server_callback(sock: socket.socket) -> None:
        # Expecting stdin/out/err
        max_fds = 3
        msglen, fds = recv_fds(sock, max_pid_len, max_fds)
        msglen = int(msglen)

        logger.debug('Received message %s, num fds: %s', msglen, len(fds))

        if len(fds) != 3:
            logger.error('Received unexpected number of fds.')
            # TODO: Determine good exit code to use and return that instead.
            return
        stdin = os.fdopen(fds[0])
        stdout = os.fdopen(fds[1], 'w')
        stderr = os.fdopen(fds[2], 'w')
        reset_loggers(stdout, stderr)
        sys.stdin = stdin
        sys.stdout = stdout
        sys.stderr = stderr

        logger.debug('Reset loggers')
        sock.sendall(f'{os.getpid()}'.encode('ascii'))
        contents = b''
        while len(contents) != msglen:
            contents += sock.recv(4096)
        state = deserialize_state(contents)
        os.chdir(state['cwd'])
        os.environ = state['env']
        sys.argv = state['argv']

        rc = callback()
        if rc is None:
            rc = 0
        sock.sendall(f'{rc}'.encode('ascii'))

    return server_callback


def _start_daemon(
        factory_fn: CliFactoryT, socket_file: Path, log_file: Path,
        pid_file: Path) -> None:
    """Start daemon process.

    Args:
        factory_fn
        socket_file
        log_file
        pid_file

    Returns:
        This function only returns in the original process, and exits
        internally in the daemon process.
    """
    logger.debug('start_daemon()')
    # We don't get any benefit the first time we're starting the daemon, so we
    # get the cli function in the parent process to avoid having to look in
    # the server log for errors.
    cli = factory_fn()
    run(_get_server_callback(cli), socket_file=socket_file, log_file=log_file,
        pid_file=pid_file)


def _run_client(socket_file: Path) -> int:
    """Run command client against daemon listening at provided `socket_file`.

    Process context includes:
    - environment
    - cwd
    - command line
    - file descriptors for stdin/out/err

    Raises:
        ConnectionRefusedError if server is not listening/available.
    """
    logger.debug('client_strategy()')

    with make_client() as sock:
        sock.connect(str(socket_file))
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

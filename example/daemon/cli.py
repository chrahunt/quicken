import logging
import os
from pathlib import Path
import socket
import sys
from typing import Callable, Optional

from .fd import max_pid_len, send_fds
from .types import CliFactoryT, NoneFunctionT
from .watch import wait_for_create


logger = logging.getLogger(__name__)


CliFactoryDecoratorT = Callable[[CliFactoryT], NoneFunctionT]


def cli_factory(
        name: str, logfile: Optional[str] = None,
        pidfile: Optional[str] = None) -> CliFactoryDecoratorT:
    """Decorator to mark a function that provides the main entrypoint.

    To benefit most from the daemon speedup, you must do required imports
    within the factory function itself and then have the returned function
    itself do a minimal amount of configuration - only those things dependent
    on e.g. environment/cwd.

    If any imported top-level modules make use of environment then they must be
    reconfigured on invokation of the cli, otherwise the environment of the
    client will not be taken into account.

    Args:
        name the name used for the socket file.
        logfile optional log file used by the server, must be absolute path
          since the server is moved to /. Default is ~/.daemon-{name}.log.
        pidfile optional pid file used by the server, must be absolute path
          since the server is moved to /. Default is ~/.daemon-{name}.pid.
    """
    socket_file = Path(os.environ['HOME']) / f'.daemon-sock-{name}'
    if logfile is None:
        logfile = Path(os.environ['HOME']) / f'.daemon-{name}.log'
    if pidfile is None:
        pidfile = Path(os.environ['HOME']) / f'.daemon-{name}.pid'

    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunctionT:
        def run_cli():
            # If the socket file exists then try to communicate with the server.
            if socket_file.exists():
                # Best case - server is already up and we could successfully
                # communicate with it,
                if client_strategy(socket_file):
                    return
            # OK case - server is not up, so we start it.
            start_daemon(factory_fn, socket_file, logfile, pidfile)
            # TODO: Make wait configurable.
            if not wait_for_create(Path(socket_file), 2):
                # Bad case - timed out waiting for server startup.
                logger.warning(
                    'Timeout waiting for daemon - executing cli directly.')
                factory_fn()()
                return

            if not client_strategy(socket_file):
                # Bad case - server came up but we could not communicate with
                # it.
                logger.warning(
                    'Failed to communicate with daemon - executing cli'
                    ' directly.')
                factory_fn()()
                return

        return run_cli

    return inner_cli_factory


def start_daemon(
        factory_fn: CliFactoryT, socket_file: Path, logfile: Path,
        pidfile: Path) -> None:
    """Start daemon process.

    Args:
        factory_fn
        socket_file
        logfile
        pidfile

    Returns:
        This function only returns in the original process, and exits
        internally in the daemon process.
    """
    logger.debug('start_daemon()')
    # We don't get any benefit the first time we're starting the daemon, so we
    # get the cli function in the parent process to avoid having to look in
    # the server log for errors.
    cli = factory_fn()
    # Late import to avoid delay if server is up.
    from .server import run
    run(cli, socket_file=socket_file, logfile=logfile, pidfile=pidfile)


def client_strategy(socket_file: Path):
    """Get daemon to start process for us, then transfer process context to it.

    Process context includes:
    - environment
    - cwd
    - command line
    """
    logger.debug('client_strategy()')

    # TODO: Use client from daemon.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(str(socket_file))
        except ConnectionRefusedError:
            logger.warning('Connection refused')
            return False
        send_fds(
            sock, f'{os.getpgrp()}'.encode('ascii'),
            [sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()])
        data = sock.recv(max_pid_len)
        logger.debug('Child finished %s', data)
    return True

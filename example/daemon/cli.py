import logging
import os
from pathlib import Path

from .watch import wait_for_create


logger = logging.getLogger(__name__)


def cli_factory(name, logfile='server.log'):
    socket_file = f'{os.environ["HOME"]}/.daemon-sock-{name}'

    def inner_cli_factory(factory_fn):
        """Decorator to mark a function that provides the main entrypoint.

        For speed you probably want to do imports inside the function itself
        before returning the method.
        """
        def run_cli():
            # If the socket file exists then try to communicate with the server.
            if Path(socket_file).exists():
                if client_strategy(socket_file):
                    return
            # Otherwise, try to bring up the server.
            start_daemon(factory_fn, socket_file)
            # Wait for daemon to come up.
            if not wait_for_create(Path(socket_file), 2):
                logger.warning('Could not bring up daemon - executing directly.')
                factory_fn()()
                return

            if not client_strategy(socket_file):
                logger.warning('Could not connect - executing directly.')
                factory_fn()()
                return
        return run_cli
    return inner_cli_factory


def start_daemon(factory_fn, socket_file):
    """Start daemon process.
    """
    # We fork here so the daemon detaching kills our child and leaves us
    # alive to act as a client.
    logger.debug('daemon_strategy()')
    if os.fork():
        return
    cli = factory_fn()
    from .server import run
    run(cli, socket_file=socket_file, working_directory=os.getcwd())


def client_strategy(socket_file):
    """Get daemon to start process for us, then transfer everything to it.
    """
    logger.debug('client_strategy()')
    import socket
    import sys

    from example.daemon.fd import max_pid_len, send_fds

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(socket_file)
        except ConnectionRefusedError:
            logger.warning('Connection refused')
            return False
        send_fds(
            sock, f'{os.getpgrp()}'.encode('ascii'),
            [sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()])
        data = sock.recv(max_pid_len)
        logger.debug('Child finished %s', data)
    return True

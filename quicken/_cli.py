"""CLI wrapper interface for starting/using server process.
"""
import json
import logging
import multiprocessing
import os
import sys

from functools import wraps
from pathlib import Path
from typing import Callable, Dict, Optional

from fasteners import InterProcessLock

from ._client import Client
from ._typing import NoneFunction
from ._constants import socket_name, server_state_name
from ._protocol import ProcessState, Request, RequestTypes
from ._signal import blocked_signals, forwarded_signals, SignalProxy
from ._xdg import cache_dir, chdir, RuntimeDir


logger = logging.getLogger(__name__)


CliFactoryT = Callable[[], NoneFunction]
BoolProvider = Callable[[], bool]


class QuickenError(Exception):
    """Generic error during server start - message has details.
    """
    pass


def _cli_factory(
        name: str,
        *,
        runtime_dir_path: Optional[str] = None,
        log_file: Optional[str] = None,
        server_idle_timeout: Optional[float] = None,
        bypass_server: BoolProvider = None,
        reload_server: BoolProvider = None,
        ):
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
        log_file: optional log file used by the server, must be an absolute
            path. If not provided the default is
            `$XDG_CACHE_HOME/quicken-{name}/server.log` or
            `$HOME/.cache/quicken-{name}/server.log`.
        server_idle_timeout: time in seconds after which the server will shut
            down if no requests are being processed.
        bypass_server: if True then run command directly instead of trying to
            use daemon.
        reload_server: if True then restart the server before executing the
            function.

    Throws:
        QuickenError: If any directory used by runtime_dir does not have the
            correct permissions.
    """
    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunction:
        @wraps(factory_fn)
        def run_cli() -> Optional[int]:
            """
            Returns:
                Result from function or remote execution, suitable for passing
                to :func:`sys.exit`.
            """
            nonlocal log_file

            if log_file is None:
                log_file = cache_dir(f'quicken-{name}') / 'server.log'
            log_file = Path(log_file).absolute()

            if bypass_server and bypass_server():
                logger.debug('Bypassing server')
                return factory_fn()()

            runtime_dir = RuntimeDir(f'quicken-{name}', runtime_dir_path)

            client = None
            with CliServerManager(
                    factory_fn, runtime_dir, log_file, server_idle_timeout) as manager:
                try:
                    client = manager.connect()
                    if reload_server and reload_server():
                        logger.debug('Reloading server')
                        client = manager.restart()
                    # TODO: Get server version.
                    # TODO: Get server context and kill without pid.
                except ConnectionRefusedError:
                    logger.warning(
                        'Failed to connect to server - executing cli directly.')

            if not client:
                multiprocessing.current_process().authkey = os.urandom(32)
                return factory_fn()()

            return _run_client(client)

        return run_cli

    return inner_cli_factory


def _cli_factory_win(
    *args,
    **kwargs,
):
    def inner_cli_factory(factory_fn: CliFactoryT) -> NoneFunction:
        @wraps(factory_fn)
        def run_cli() -> Optional[int]:
            return factory_fn()()

        return run_cli

    return inner_cli_factory


if sys.platform.startswith('win'):
    cli_factory = _cli_factory_win
else:
    cli_factory = _cli_factory


class CliServerManager:
    """Responsible for starting (if applicable) and connecting to the server.

    Race conditions are prevented by acquiring an exclusive lock on
    {runtime_dir}/admin during connection and start.
    """
    def __init__(
            self, factory_fn, runtime_dir: RuntimeDir, log_file,
            server_idle_timeout):
        """
        Args:
            factory_fn: function that provides the server request handler
            runtime_dir: runtime dir used for locks/socket
            log_file: server log file
            server_idle_timeout: idle timeout communicated to server if the
                process of connecting results in server start
        """
        self._factory = factory_fn
        self._runtime_dir = runtime_dir
        self._log_file = log_file
        self._idle_timeout = server_idle_timeout
        self._lock = InterProcessLock('admin')
        # We initialize the Client and Listener classes without an authkey
        # parameter since there's no way to pre-share the secret securely
        # between processes not part of the same process tree. However, the
        # internal Client/Listener used as part of
        # multiprocessing.resource_sharer DOES initialize its own Client and
        # Listener with multiprocessing.current_process().authkey. We must have
        # some value so we use this dummy value.
        multiprocessing.current_process().authkey = b'0' * 32

    def connect(self) -> Client:
        """Attempt to connect to the server, starting it if required.

        Args:
            timeout: seconds to wait for successful connection or startup

        Returns:
            Client connected to the server
        """
        assert self._lock.acquired, 'Connect must be called under lock.'
        try:
            return self._get_client()
        except FileNotFoundError:
            # Server not up, no problem, we'll try to start it.
            logger.debug('Server not up, starting it.')
        except ConnectionRefusedError:
            socket_file = self._runtime_dir.path(socket_name)
            # Server may have died unexpectedly.
            logger.warning('Could not connect to server, starting it.')
            # Clean up the socket file before proceeding.
            socket_file.unlink()

        self._start_server()

        # Try to connect again, this time we don't catch anything, leave it to
        # the caller.
        return self._get_client()

    def restart(self) -> Client:
        """Restart the server and reconnect.
        """
        self._stop_server()
        return self.connect()

    def _get_client(self) -> Client:
        """
        Raises:
            FileNotFoundError if the socket file is not present
            ConnectionRefusedError if the socket file is present but the server
                is not accepting connections
        """
        with chdir(self._runtime_dir):
            return Client(socket_name)

    def _get_server_state(self) -> Dict:
        """Retrieve server state data.
        """
        with chdir(self._runtime_dir):
            return json.loads(
                Path(server_state_name).read_text(encoding='utf-8'))

    def _stop_server(self):
        from psutil import NoSuchProcess, Process
        server_state = self._get_server_state()
        pid = server_state['pid']
        create_time = server_state['create_time']
        try:
            process = Process(pid=pid)
        except NoSuchProcess:
            logger.debug(
                f'Daemon reload requested but process with pid {pid}'
                ' does not exist.')
            return

        if process.create_time() != create_time:
            logger.debug(
                'Daemon reload requested but start time does not match'
                ' expected (probably new process re-using pid), skipping.')
            return

        try:
            # We don't want to leave it to the server to remove the socket since
            # we do not wait for it.
            with chdir(self._runtime_dir):
                os.unlink(socket_name)
        except FileNotFoundError:
            # No problem, if the file was removed at some point it doesn't
            # impact us.
            pass
        # This will cause the server to stop accepting clients and start
        # shutting down. It will wait for any still-running processes before
        # stopping completely, but it does not consume any other resources that
        # we are concerned with.
        process.terminate()

    def _start_server(self):
        """Start server as background process.

        The socket for the server has been created by the time this function
        returns.

        This function only returns in the parent, not the background process.
        """
        cli = self._factory()
        # Lazy import so we only take the time to import if we have to start
        # the server.
        from ._server import run
        run(
            cli, log_file=self._log_file,
            runtime_dir=self._runtime_dir,
            server_idle_timeout=self._idle_timeout)

    def __enter__(self):
        """Enter the server admin lock context.
        """
        with chdir(self._runtime_dir):
            # TODO: Ensure that this creates our lock file with 700 since
            #  otherwise it might not be respected.
            self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the server admin lock context.
        """
        self._lock.release()


def _run_client(client: Client) -> int:
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
    logger.debug('Starting client communication')
    # Assume that we've already vetted the server and now we just need to run
    # the process.

    proxy = SignalProxy()
    # We must block signals before requesting remote process start otherwise
    # a user signal to the client may race with our ability to propagate it.
    with blocked_signals(forwarded_signals):
        state = ProcessState.for_current_process()
        logger.debug('Requesting process start')
        req = Request(RequestTypes.run_process, state)
        response = client.send(req)
        pid = response.contents
        logger.debug('Process running with pid: %d', pid)
        proxy.set_target(pid)

    logger.debug('Waiting for process to finish')
    response = client.send(Request(RequestTypes.wait_process_done, None))
    client.close()
    return response.contents

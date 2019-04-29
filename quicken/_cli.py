"""CLI wrapper interface for starting/using server process.
"""
import json
import logging
import multiprocessing
import os
import sys

from contextlib import contextmanager
from functools import partial, wraps
from pathlib import Path
from typing import Callable, Dict, Optional

from fasteners import InterProcessLock

from ._client import Client
from ._constants import socket_name, server_state_name
from ._protocol import ProcessState, Request, RequestTypes
from ._signal import blocked_signals, forwarded_signals, SignalProxy
from ._typing import JSONValue, NoneFunction
from ._xdg import cache_dir, chdir, RuntimeDir


logger = logging.getLogger(__name__)


MainFunction = Callable[[], Optional[int]]
MainProvider = Callable[[], MainFunction]
CliFactoryT = Callable[[], NoneFunction]
JSONDict = Dict[str, JSONValue]


class QuickenError(Exception):
    """Generic error during server start - message has details.
    """
    pass


def check_res_ids():
    ruid, euid, suid = os.getresuid()
    if not ruid == euid == suid:
        raise QuickenError(
            f'real uid ({ruid}), effective uid ({euid}), and saved uid ({suid})'
            ' must be the same'
        )

    rgid, egid, sgid = os.getresgid()
    if not rgid == egid == sgid:
        raise QuickenError(
            f'real gid ({rgid}), effective gid ({egid}), and saved gid ({sgid})'
            ' must be the same'
        )


def need_server_reload(manager, reload_server, user_data):
    server_state = manager.server_state
    gid = os.getgid()
    if gid != server_state['gid']:
        logger.info('Reloading server due to gid change')
        return True

    # XXX: Will not have the intended effect on macOS, see os.getgroups() for
    #  details.
    groups = os.getgroups()
    if set(groups) != set(server_state['groups']):
        logger.info('Reloading server due to changed groups')
        return True

    if reload_server:
        previous_user_data = manager.user_data
        if reload_server(previous_user_data, user_data):
            logger.info('Reload requested by callback, stopping server.')
            return True

    # TODO: Restart based on library version.
    return False


def _server_runner_wrapper(
    name: str,
    main_provider: MainProvider,
    # /,
    *,
    runtime_dir_path: Optional[str] = None,
    log_file: Optional[str] = None,
    server_idle_timeout: Optional[float] = None,
    reload_server: Callable[[JSONDict, JSONDict], bool] = None,
    user_data: JSONDict = None,
) -> Optional[int]:
    """Run operation in server identified by name, starting it if required.
    """

    check_res_ids()

    if log_file is None:
        log_file = cache_dir(f'quicken-{name}') / 'server.log'
        log_file = Path(log_file).absolute()

    main_provider = partial(with_reset_authkey, main_provider)

    runtime_dir = RuntimeDir(f'quicken-{name}', runtime_dir_path)

    manager = CliServerManager(
        main_provider, runtime_dir, log_file, server_idle_timeout, user_data
    )

    with manager.lock:
        need_start = False
        try:
            client = manager.connect()
        except ConnectionFailed as e:
            logger.info('Failed to connect to server due to %s.', e)
            need_start = True
        else:
            if need_server_reload(manager, reload_server, user_data):
                manager.stop_server()
                need_start = True

        if need_start:
            logger.info('Starting server')
            manager.start_server()
            client = manager.connect()

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


def _cli_factory(
    name: str,
    # /,
    *,
    runtime_dir_path: Optional[str] = None,
    log_file: Optional[str] = None,
    server_idle_timeout: Optional[float] = None,
    bypass_server: Callable[[], bool] = None,
    reload_server: Callable[[JSONDict, JSONDict], bool] = None,
    user_data: JSONDict = None,
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
        user_data: JSON-serializable data provided to reload_server

    Throws:
        QuickenError: If any directory used by runtime_dir does not have the
            correct permissions.
    """

    try:
        json.dumps(user_data)
    except TypeError as e:
        raise QuickenError('user_data must be serializable') from e

    def function_handler(main_provider: MainProvider) -> MainFunction:
        @wraps(main_provider)
        def wrapper() -> Optional[int]:
            if bypass_server and bypass_server():
                logger.debug('Bypassing server by request.')
                return main_provider()()

            return _server_runner_wrapper(
                name,
                main_provider,
                runtime_dir_path=runtime_dir_path,
                log_file=log_file,
                server_idle_timeout=server_idle_timeout,
                reload_server=reload_server,
                user_data=user_data,
            )

        return wrapper

    return function_handler


def _cli_factory_win(*_, **__):
    def function_handler(main_provider: MainProvider) -> MainFunction:
        return main_provider()

    return function_handler


if sys.platform.startswith('win'):
    cli_factory = _cli_factory_win
else:
    cli_factory = _cli_factory


def reset_authkey():
    multiprocessing.current_process().authkey = os.urandom(32)


def with_reset_authkey(main_provider):
    """Ensure that user code is not executed without an authkey set.
    """
    main = main_provider()

    def inner():
        reset_authkey()
        return main()

    return inner


class ConnectionFailed(Exception):
    pass


class CliServerManager:
    """Responsible for starting (if applicable) and connecting to the server.

    Race conditions are prevented by acquiring an exclusive lock on
    {runtime_dir}/admin during connection and start.
    """
    def __init__(
            self, factory_fn, runtime_dir: RuntimeDir, log_file,
            server_idle_timeout, user_data):
        """
        Args:
            factory_fn: function that provides the server request handler
            runtime_dir: runtime dir used for locks/socket
            log_file: server log file
            server_idle_timeout: idle timeout communicated to server if the
                process of connecting results in server start
            user_data: added to server state
        """
        self._factory = factory_fn
        self._runtime_dir = runtime_dir
        self._log_file = log_file
        self._idle_timeout = server_idle_timeout
        self._user_data = user_data
        self._lock = InterProcessLock('admin')

    def connect(self) -> Client:
        """Attempt to connect to the server.

        Returns:
            Client connected to the server

        Raises:
            ConnectionFailed on connection failure (server not up or accepting)
        """
        assert self._lock.acquired, 'connect must be called under lock.'

        with chdir(self._runtime_dir):
            try:
                return Client(socket_name)
            except FileNotFoundError as e:
                raise ConnectionFailed('File not found') from e
            except ConnectionRefusedError as e:
                raise ConnectionFailed('Connection refused') from e

    @property
    def server_state(self):
        with chdir(self._runtime_dir):
            text = Path(server_state_name).read_text(encoding='utf-8')
        return json.loads(text)

    @property
    def user_data(self):
        """Returns user data for the current server.
        """
        return self.server_state['user_data']

    def start_server(self):
        """Start server as background process.

        This function only returns in the parent, not the background process.

        By the time this function returns it is safe to call connect().
        """
        assert self._lock.acquired, 'start_server must be called under lock.'
        # XXX: Should have logging around this, for timing.
        main = self._factory()
        # Lazy import so we only take the time to import if we have to start
        # the server.
        # XXX: Should have logging around this, for timing.
        from ._server import run

        with chdir(self._runtime_dir):
            try:
                os.unlink(socket_name)
            except FileNotFoundError:
                pass

        run(
            main,
            log_file=self._log_file,
            runtime_dir=self._runtime_dir,
            server_idle_timeout=self._idle_timeout,
            user_data=self._user_data,
        )

    def stop_server(self):
        assert self._lock.acquired, 'stop_server must be called under lock.'
        from psutil import NoSuchProcess, Process
        server_state = self.server_state
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

    @property
    @contextmanager
    def lock(self):
        with chdir(self._runtime_dir):
            self._lock.acquire()

        # We initialize the Client and Listener classes without an authkey
        # parameter since there's no way to pre-share the secret securely
        # between processes not part of the same process tree. However, the
        # internal Client/Listener used as part of
        # multiprocessing.resource_sharer DOES initialize its own Client and
        # Listener with multiprocessing.current_process().authkey. We must have
        # some value so we use this dummy value.
        multiprocessing.current_process().authkey = b'0' * 32

        try:
            yield
        finally:
            self._lock.release()

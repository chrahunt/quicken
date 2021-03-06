"""CLI wrapper interface for starting/using server process.
"""
from __future__ import annotations

import json
import logging
import os
import socket

from contextlib import contextmanager

from . import QuickenError
from ._imports import InterProcessLock
from ._multiprocessing_reduction import set_fd_sharing_base_path_fd
from ._signal import blocked_signals, forwarded_signals, SignalProxy
from ._typing import MYPY_CHECK_RUNNING
from .client import Client
from .constants import socket_name, server_state_name, stop_socket_name
from .protocol import ProcessState, Request, RequestTypes
from .timings import report
from .xdg import chdir, RuntimeDir
from .. import __version__, set_importing

if MYPY_CHECK_RUNNING:
    from typing import Any, Callable, Dict, Optional

    from ._types import JSONType, MainProvider


logger = logging.getLogger(__name__)


def check_res_ids():
    ruid, euid, suid = os.getresuid()
    if not ruid == euid == suid:
        raise QuickenError(
            f"real uid ({ruid}), effective uid ({euid}), and saved uid ({suid})"
            " must be the same"
        )

    rgid, egid, sgid = os.getresgid()
    if not rgid == egid == sgid:
        raise QuickenError(
            f"real gid ({rgid}), effective gid ({egid}), and saved gid ({sgid})"
            " must be the same"
        )


def need_server_reload(manager, reload_server, user_data):
    server_state = manager.server_state_raw
    # Check version first, in case other key fields may have changed.
    if __version__ != server_state["lib_version"]:
        logger.info("Reloading due to library version change")
        return True

    gid = os.getgid()
    if gid != server_state["gid"]:
        logger.info("Reloading server due to gid change")
        return True

    # XXX: Will not have the intended effect on macOS, see os.getgroups() for
    #  details.
    groups = os.getgroups()
    if set(groups) != set(server_state["groups"]):
        logger.info("Reloading server due to changed groups")
        return True

    if reload_server:
        previous_user_data = manager.user_data
        if reload_server(previous_user_data, user_data):
            logger.info("Reload requested by callback, stopping server.")
            return True

    return False


def server_runner_wrapper(
    name: str,
    main_provider: MainProvider,
    # /,
    *,
    runtime_dir_path: Optional[str] = None,
    server_idle_timeout: Optional[float] = None,
    reload_server: Callable[[JSONType, JSONType], bool] = None,
    user_data: JSONType = None,
) -> Optional[int]:
    """Run operation in server identified by name, starting it if required.
    """

    check_res_ids()

    try:
        json.dumps(user_data)
    except TypeError as e:
        raise QuickenError("user_data must be serializable") from e

    try:
        runtime_dir = RuntimeDir(name, runtime_dir_path)
    except RuntimeError as e:
        raise QuickenError(e.args[0]) from None

    set_fd_sharing_base_path_fd(runtime_dir.fileno())

    manager = ServerManager(runtime_dir)

    report("connecting to server")
    with manager.lock:
        need_start = False
        try:
            client = manager.connect()
        except ConnectionFailed as e:
            logger.info("Failed to connect to server due to %s.", e)
            need_start = True
        else:
            if need_server_reload(manager, reload_server, user_data):
                manager.stop_server()
                need_start = True

        if need_start:
            set_importing(True)
            report("starting user code import")
            main = main_provider()
            report("end user code import")
            set_importing(False)
            report("starting server")
            manager.start_server(main, server_idle_timeout, user_data)
            report("connecting to server")
            try:
                client = manager.connect()
            except ConnectionFailed:
                logger.exception("failed to connect to server")
                raise QuickenError("failed to connect to server")

    report("connected to server")
    proxy = SignalProxy()
    # We must block signals before requesting remote process start otherwise
    # a user signal to the client may race with our ability to propagate it.
    with blocked_signals(forwarded_signals):
        state = ProcessState.for_current_process()
        report("requesting start")
        logger.debug("Requesting process start")
        req = Request(RequestTypes.run_process, state)
        response = client.send(req)
        pid = response.contents
        report("process started")
        logger.debug("Process running with pid: %d", pid)
        proxy.set_target(pid)

    # At this point user code is running.
    logger.debug("Waiting for process to finish")
    req = Request(RequestTypes.wait_process_done, None)
    response = client.send(req)
    client.close()
    return response.contents


class ConnectionFailed(Exception):
    pass


class ServerState:
    def __init__(self, running: bool, attributes: Dict[str, Any] = None):
        self.running = running
        self.attributes = attributes or {}


class ServerManager:
    """Responsible for starting (if applicable) and connecting to the server.

    Race conditions are prevented by acquiring an exclusive lock on
    {runtime_dir}/admin during connection and start.
    """

    def __init__(self, runtime_dir: RuntimeDir):
        """
        Args:
            runtime_dir: runtime dir used for locks/socket
        """
        self._runtime_dir = runtime_dir
        self._lock = InterProcessLock("admin")

    def connect(self) -> Client:
        """Attempt to connect to the server.

        Returns:
            Client connected to the server

        Raises:
            ConnectionFailed on connection failure (server not up or accepting)
        """
        assert self._lock.acquired, "connect must be called under lock."

        with chdir(self._runtime_dir):
            try:
                return Client(socket_name)
            except FileNotFoundError as e:
                raise ConnectionFailed("File not found") from e
            except ConnectionRefusedError as e:
                raise ConnectionFailed("Connection refused") from e

    @property
    def server_state_raw(self) -> Dict[str, Any]:
        """Raw state as read from disk.
        """
        with chdir(self._runtime_dir):
            with open(server_state_name, encoding="utf-8") as f:
                text = f.read()
        return json.loads(text)

    @property
    def server_running(self) -> bool:
        try:
            client = self.connect()
        except ConnectionFailed:
            pass
        else:
            client.close()
            return True
        return False

    @property
    def server_state(self) -> ServerState:
        """Combined, preprocessed server state for CLI.
        """
        server_running = self.server_running
        state = {"status": "up" if server_running else "down"}

        if server_running:
            state.update(self.server_state_raw)

        return ServerState(server_running, state)

    @property
    def user_data(self):
        """Returns user data for the current server.
        """
        return self.server_state_raw["user_data"]

    def start_server(self, main, server_idle_timeout, user_data):
        """Start server as background process.

        This function only returns in the parent, not the background process.

        By the time this function returns it is safe to call connect().

        Args:
            main: function that provides the server request handler
            server_idle_timeout: idle timeout communicated to server if the
                process of connecting results in server start
            user_data: added to server state
        """
        assert self._lock.acquired, "start_server must be called under lock."
        # Lazy import so we only take the time to import if we have to start
        # the server.
        report("start server import")
        from .server import run

        report("end server import")

        with chdir(self._runtime_dir):
            try:
                os.unlink(socket_name)
            except FileNotFoundError:
                pass

            try:
                os.unlink(stop_socket_name)
            except FileNotFoundError:
                pass

        run(
            main,
            runtime_dir=self._runtime_dir,
            server_idle_timeout=server_idle_timeout,
            user_data=user_data,
        )

    def stop_server(self):
        assert self._lock.acquired, "stop_server must be called under lock."

        # We don't want to leave it to the server to remove the sockets since
        # we do not wait for it to shut down before starting a new one.
        try:
            with chdir(self._runtime_dir):
                os.unlink(socket_name)
        except FileNotFoundError:
            # If any file was removed it doesn't impact us.
            pass

        try:
            # This will cause the server to stop accepting clients and start
            # shutting down. It will wait for any still-running processes before
            # stopping completely, but it does not consume any other resources
            # that we are concerned with.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(stop_socket_name)
        except ConnectionRefusedError:
            # Best effort.
            pass
        except FileNotFoundError:
            # No problem, server not up.
            pass

        try:
            with chdir(self._runtime_dir):
                os.unlink(stop_socket_name)
        except FileNotFoundError:
            # If any file was removed it doesn't impact us.
            pass

    @property
    @contextmanager
    def lock(self):
        with chdir(self._runtime_dir):
            self._lock.acquire()

        try:
            yield
        finally:
            self._lock.release()

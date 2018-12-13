import logging.config
import os
import signal
import uuid

import pytest

from quicken.server import make_client, run
from quicken.watch import wait_for_create, wait_for_delete

from .utils import contained_children, isolated_filesystem


logger = logging.getLogger(__name__)


def noop(*_args, **_kwargs):
    pass


def run_server(*args, **kwargs):
    """Run `run` in a compatible way.

    Requires pytest be invoked with `-s`, otherwise python-daemon fails to dup
    stdin/out/err.
    """
    # When debugging in PyCharm, closerange takes forever so we replace it
    # here.
    os.closerange = noop
    return run(*args, **kwargs)


def test_daemon_starts():
    def noop_request_handler(_sock):
        pass
    with isolated_filesystem() as path:
        pid_file = path / 'daemon.pid'
        socket_file = path / 'socket'
        with contained_children():
            pid = run_server(
                noop_request_handler, socket_file=socket_file,
                pid_file=pid_file)
            assert wait_for_create(pid_file)
            assert pid_file.exists()
            pid_from_file = pid_file.read_text(encoding='utf-8')
            assert str(pid) == pid_from_file.strip()
            os.kill(pid, signal.SIGTERM)
            assert wait_for_delete(pid_file)


@pytest.mark.skip
def test_daemon_communicates():
    with isolated_filesystem() as path:
        def write_file(handler_sock):
            socket_data = handler_sock.recv(1024).decode('utf-8')
            output_file.write_text(socket_data, encoding='utf-8')

        pid_file = path / 'daemon.pid'
        socket_file = path / 'socket'
        output_file = path / 'test.txt'
        with contained_children():
            pid = run_server(
                write_file, socket_file=socket_file, pid_file=pid_file)

            assert wait_for_create(pid_file, 10)
            assert wait_for_create(socket_file)
            data = str(uuid.uuid4())
            with make_client() as sock:
                sock.connect(str(socket_file))
                sock.sendall(data.encode('utf-8'))
            assert wait_for_create(output_file)
            contents = output_file.read_text(encoding='utf-8')
            os.kill(pid, signal.SIGTERM)
            print(f'File exists: {pid_file.exists()}')
            result = wait_for_delete(pid_file)
            print(f'File exists: {pid_file.exists()}')
            print(f'Result: {result}')
    assert contents == data


def server_correctly_logs_unhandled_exceptions():
    # Given a bug leading to exception in the handler implementation in daemon/server.py
    # When the server encounters the error
    # Then it will trace the error to the log file
    ...

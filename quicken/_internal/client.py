from __future__ import annotations

import socket

from ._imports import multiprocessing_connection
from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from .protocol import Request, Response


def make_client(address):
    """Create Client with a timeout.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(address)
        return multiprocessing_connection.Connection(s.detach())


class Client:
    """Enforces a request/response protocol on top of
    multiprocessing.connection.Client.
    """
    def __init__(self, address):
        self._client = make_client(address)

    def send(self, request: Request) -> Response:
        self._client.send(request)
        return self._client.recv()

    def close(self) -> None:
        self._client.close()

    def settimeout(self, arg):
        with socket.fromfd(
            self._client.fileno(), socket.AF_UNIX, socket.SOCK_STREAM
        ) as s:
            s.settimeout(arg)
            s.detach()

from __future__ import annotations

from ._imports import multiprocessing_connection
from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from ._protocol import Request, Response


class Client:
    """Enforces a request/response protocol on top of
    multiprocessing.connection.Client.
    """
    def __init__(self, *args, **kwargs):
        self._client = multiprocessing_connection.Client(*args, **kwargs)

    def send(self, request: Request) -> Response:
        self._client.send(request)
        return self._client.recv()

    def close(self) -> None:
        self._client.close()

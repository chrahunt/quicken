import multiprocessing.connection

from ._protocol import Request, Response


class Client:
    """Enforces a request/response protocol on top of
    multiprocessing.connection.Client.
    """
    def __init__(self, *args, **kwargs):
        self._client = multiprocessing.connection.Client(*args, **kwargs)

    def send(self, request: Request) -> Response:
        self._client.send(request)
        return self._client.recv()

    def close(self) -> None:
        self._client.close()

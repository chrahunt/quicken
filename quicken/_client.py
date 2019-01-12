import multiprocessing.connection

from .protocol import Request, Response


class Client:
    def __init__(self, *args, **kwargs):
        self._client = multiprocessing.connection.Client(*args, **kwargs)

    def send(self, request: Request) -> Response:
        self._client.send(request)
        return self._client.recv()

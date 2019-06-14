"""Entrypoint wrapper that represents a control script.

Valid commands are:

* stop
* status
"""
import sys

from .lib._lib import CliServerManager, ConnectionFailed
from .lib._xdg import RuntimeDir
from ._scripts import wrapper_script


__all__ = []


def parse_args():
    if len(sys.argv) == 1:
        sys.stderr.write(
            f'Usage: {sys.argv[0]} stop|status'
        )
        sys.exit(1)

    return sys.argv[1]


def callback(helper):
    name = helper.name

    runtime_dir = RuntimeDir(name)

    manager = CliServerManager(runtime_dir)

    action = parse_args()

    with manager.lock:
        try:
            client = manager.connect()
        except ConnectionFailed:
            print('Server down')
            sys.exit(0)
        else:
            client.close()

        if action == 'status':
            print(manager.server_state)
        elif action == 'stop':
            manager.stop_server()
        else:
            sys.stderr.write('Unknown action')
            sys.exit(1)


sys.modules[__name__] = wrapper_script(callback)

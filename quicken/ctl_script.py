"""Entrypoint wrapper that represents a control script.

Valid commands are:

* stop
* status
"""
from .lib._lib import CliServerManager, ConnectionFailed
from .lib._xdg import RuntimeDir
from ._scripts import (
    get_attribute_accumulator, parse_script_spec
)

import sys


__all__ = []


def parse_args():
    if len(sys.argv) == 1:
        sys.stderr.write(
            f'Usage: {sys.argv[0]} stop|status'
        )
        sys.exit(1)

    return sys.argv[1]


def callback(parts):
    module_parts, function_parts = parse_script_spec(parts)
    module_name = '.'.join(module_parts)
    function_name = '.'.join(function_parts)
    # TODO: De-duplicate key creation.
    name = f'quicken.entrypoint.{module_name}.{function_name}'

    # TODO: De-duplicate runtime dir name construction.
    runtime_dir = RuntimeDir(f'quicken-{name}')

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

sys.modules[__name__] = get_attribute_accumulator(callback)

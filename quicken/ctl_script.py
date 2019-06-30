"""Entrypoint wrapper that represents a control script.

Valid commands are:

* stop
* status
"""
from __future__ import annotations

import argparse
import json
import sys

from .lib._lib import CliServerManager, ConnectionFailed
from .lib._typing import MYPY_CHECK_RUNNING
from .lib._xdg import RuntimeDir
from ._scripts import wrapper_script

if MYPY_CHECK_RUNNING:
    from typing import Dict


# No public API.
__all__ = []


def parse_args():
    parser = argparse.ArgumentParser(description='Control and query a quicken server.')
    subparsers = parser.add_subparsers(
        description='',
        dest='action',
        metavar='<subcommand>',
        required=True,
    )

    status_parser = subparsers.add_parser(
        'status', description='Get server status.', help='get server status'
    )
    status_parser.add_argument('--json', action='store_true', help='output status data as JSON')

    subparsers.add_parser(
        'stop', description='Stop server.', help='stop server if it is running'
    )

    return parser.parse_args()


def pretty_state(state):
    def pretty_object(o: Dict, indent=0):
        indent_text = '  ' * indent
        lines = []
        for k in sorted(o.keys()):
            v = o[k]
            pretty_k = pretty_key(k)
            if isinstance(v, dict):
                lines.append(f'{indent_text}{pretty_k}:')
                lines.extend(pretty_object(v, indent + 1))
            else:
                lines.append(f'{indent_text}{pretty_k}: {v!r}')
        return lines

    def pretty_key(k: str):
        return k.replace('_', ' ').title()

    return '\n'.join(pretty_object(state))


def callback(helper):
    name = helper.name

    runtime_dir = RuntimeDir(name)

    manager = CliServerManager(runtime_dir)

    args = parse_args()

    with manager.lock:
        server_running = False
        try:
            client = manager.connect()
        except ConnectionFailed:
            pass
        else:
            server_running = True
            client.close()

        if args.action == 'status':
            state = {
                'status': 'up' if server_running else 'down',
            }

            if server_running:
                state.update(manager.server_state)

            if args.json:
                print(json.dumps(state, sort_keys=True, separators=(',', ':')))
            else:
                print(pretty_state(state))

        elif args.action == 'stop':
            if server_running:
                print('Stopping...')
                manager.stop_server()
                print('Stopped.')
            else:
                print('Server already down.')


# See .script for explanation.
sys.modules[__name__] = wrapper_script(callback)

"""Entrypoint wrapper that represents a control script.

Valid commands are:

* stop
* status
"""
from __future__ import annotations

import argparse
import sys

from ._internal.cli.helpers import CliServerManager, Commands
from ._internal.entrypoints import wrapper_script
from ._internal.lib import ServerManager
from ._internal.xdg import RuntimeDir


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
        Commands.STATUS, description='Get server status.', help='get server status'
    )
    status_parser.add_argument('--json', action='store_true', help='output status data as JSON')

    subparsers.add_parser(
        Commands.STOP, description='Stop server.', help='stop server if it is running'
    )

    return parser.parse_args()


def callback(helper):
    name = helper.name

    runtime_dir = RuntimeDir(name)

    manager = ServerManager(runtime_dir)

    args = parse_args()

    with manager.lock:
        cli_manager = CliServerManager(manager, sys.stdout)

        if args.action == Commands.STATUS:
            cli_manager.print_status(json_format=args.json)

        elif args.action == Commands.STOP:
            cli_manager.stop()


# See .script for explanation.
sys.modules[__name__] = wrapper_script(callback)

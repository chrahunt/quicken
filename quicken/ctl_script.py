"""Entrypoint wrapper that represents a control script.

Valid commands are:

* stop
* status
"""
from __future__ import annotations

import argparse
import sys

from ._internal.cli.cli import (
    add_logging_options,
    ConfigurationError,
    handle_logging_options,
)
from ._internal.cli.helpers import CliServerManager, Commands
from ._internal.entrypoints import ConsoleScriptHelper, console_script
from ._internal.lib import ServerManager
from ._internal.xdg import RuntimeDir


# No public API.
__all__ = []


def get_arg_parser():
    parser = argparse.ArgumentParser(description="Control and query a quicken server.")
    subparsers = parser.add_subparsers(
        description="", dest="action", metavar="<subcommand>", required=True
    )

    status_parser = subparsers.add_parser(
        Commands.STATUS, description="Get server status.", help="get server status"
    )
    add_logging_options(status_parser)
    status_parser.add_argument(
        "--json", action="store_true", help="output status data as JSON"
    )

    stop_parser = subparsers.add_parser(
        Commands.STOP, description="Stop server.", help="stop server if it is running"
    )
    add_logging_options(stop_parser)

    return parser


def callback(helper: ConsoleScriptHelper):
    name = helper.name

    runtime_dir = RuntimeDir(name)

    manager = ServerManager(runtime_dir)

    parser = get_arg_parser()
    args = parser.parse_args()

    try:
        handle_logging_options(args)
    except ConfigurationError as e:
        parser.error(e.args[0])

    with manager.lock:
        cli_manager = CliServerManager(manager, sys.stdout)

        if args.action == Commands.STATUS:
            cli_manager.print_status(json_format=args.json)

        elif args.action == Commands.STOP:
            cli_manager.stop()


# See .script for explanation.
sys.modules[__name__] = console_script(callback)

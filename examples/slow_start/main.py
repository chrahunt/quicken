"""Wrapper around actual entrypoint.
"""
import logging
import os

from quicken import cli_factory


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def disabled() -> bool:
    return os.environ.get('SPEEDY_CLI_USE_DAEMON', 'yes').lower() in [
        '0', 'no', 'false', 'f', ''
    ]


def reload() -> bool:
    return os.environ.get('SPEEDY_CLI_RELOAD_DAEMON', 'no').lower() in [
        '1', 'yes', 'y', 'please'
    ]


@cli_factory('quicken', bypass_server=disabled, reload_server=reload)
def main():
    logger.debug('main()')
    from .app import cli
    return cli

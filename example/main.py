"""Wrapper around actual entrypoint.
"""
from .daemon.cli import cli_factory


@cli_factory('example')
def main():
    from .app import cli
    return cli

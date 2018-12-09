import logging
import os
from pathlib import Path

# Example packages that take some 500ms to load.
import click
import conans
import requests


logger = logging.getLogger(__name__)


def cli():
    """Actual application entrypoint.
    """
    print('cli()')
    Path('/tmp/app-output.txt').write_text(str(os.getpid()))
    logger.info('cli()')

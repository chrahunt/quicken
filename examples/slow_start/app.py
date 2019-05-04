import logging
import time

logger = logging.getLogger(__name__)


# Represents module import/load time.
time.sleep(0.5)


def cli():
    """Actual application entrypoint.
    """
    print('cli()')
    print('cli2()')
    logger.info('cli()')


if __name__ == '__main__':
    cli()

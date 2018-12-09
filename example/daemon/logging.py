import logging
import sys


def setup_daemon_logging(filename, level):
    pass


def reset_loggers(stdout, stderr):
    """Reset any StreamHandlers on any configured loggers.
    """
    def reset_handlers(logger):
        for h in logger.handlers:
            print('handler')
            if isinstance(h, logging.StreamHandler):
                # Check if using stdout/stderr in underlying stream and call
                # setStream if so.
                # TODO: setStream in Python 3.7
                if h.stream == sys.stdout:
                    h.stream = stdout
                elif h.stream == sys.stderr:
                    h.stream = stderr

    loggers = logging.Logger.manager.loggerDict
    for _, logger in loggers.items():
        reset_handlers(logger)
    reset_handlers(logging.getLogger())

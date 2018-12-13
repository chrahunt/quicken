import io
import logging

from quicken.logging import reset_loggers


def test_logging_stream_override_works():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.level = logging.DEBUG
    logger.addHandler(handler)

    new_stdout = io.StringIO()
    new_stderr = io.StringIO()
    reset_loggers(new_stdout, new_stderr)
    logger.debug('Example message')
    assert 'Example message' in new_stderr.getvalue()

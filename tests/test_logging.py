import io
import logging

import pytest

from quicken.lib._protocol import ProcessState

from .utils.pytest import non_windows


pytestmark = non_windows


def test_logging_stream_override_works():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.level = logging.DEBUG
    logger.addHandler(handler)

    new_stdout = io.StringIO()
    new_stderr = io.StringIO()
    ProcessState._reset_loggers(new_stdout, new_stderr)
    logger.debug('Example message')
    assert 'Example message' in new_stderr.getvalue()


@pytest.mark.skip
def test_logging_exceptions():
    # Given there is a formatting error in a logging statement
    # And the default quicken logging configuration is enabled
    # When the logging statement is executed
    # Then the exception will be traced in the log
    ...

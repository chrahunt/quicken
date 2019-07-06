"""Primary tests of CLI wrapper functionality.

The purpose of the library is to transparently start a server that runs the
provided function in response to client requests.

Our tests generally follow the format:

```python
def test_something():
    # Gherkin-like high-level description of what the test steps look like.

    @cli_factory(test_function_name())
    # This is the function that gets executed. The library transparently runs
    # the returned function in the server. On the test side invoking this
    # function should return the expected return  value of 'inner'.
    def runner():
        # This is the function that actually gets executed by the server and
        # will run in a separate process.
        def inner():
            # Write to file or do something that can be checked in the original
            # test process.
            ...

        # This is the return value consumed by the library.
        return inner

    # Ensures that a failing test doesn't leave the server or any handler
    # processes running.
    with contained_children():
        assert runner() == 0
        # Next check whether whatever was written by the `inner` function is
        # as expected.
        ...
```
"""
from quicken._internal.decorator import quicken
from quicken._internal._logging import default_configuration, reset_configuration

from ..conftest import get_log_file
from ..utils import env, preserved_signals
from ..utils.pytest import current_test_name


_default_name = object()


def cli_factory(name=_default_name, **kwargs):
    """Test function wrapper.
    """
    # Consistent log file naming for server output.
    if name is _default_name:
        name = current_test_name()

    # Preserve normal signal handlers in callback function so it does not bleed
    # into forked processes between multiple tests.
    def wrapper(func):
        def execute_with_preserved_signals():
            with preserved_signals():
                return func()

        def logging_setup_wrapper():
            with env(QUICKEN_LOG=str(get_log_file())):
                default_configuration()
            try:
                return quicken(name, **kwargs)(execute_with_preserved_signals)()
            finally:
                reset_configuration()

        return logging_setup_wrapper

    return wrapper

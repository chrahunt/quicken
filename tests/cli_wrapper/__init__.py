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
from pathlib import Path

from quicken import cli_factory as _cli_factory

from ..utils.pytest import current_test_name
from ..utils import preserved_signals


log_dir = Path('logs').absolute()


def cli_factory(*args, **kwargs):
    """Test function wrapper.
    """
    # Consistent log file naming for server output.
    log_file = log_dir / f'{current_test_name()}-server.log'
    kwargs['log_file'] = log_file
    # Preserve normal signal handlers in callback function so it does not bleed
    # into forked processes between multiple tests.
    def wrapper(func):
        def execute_with_preserved_signals():
            with preserved_signals():
                return func()
        return _cli_factory(*args, **kwargs)(execute_with_preserved_signals)

    return wrapper

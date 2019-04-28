import subprocess
import sys
import uuid

from .utils import env
from .utils.pytest import current_test_name, non_windows


pytestmark = non_windows


test_program = '''
import time
import sys

# Represents imports and module initialization.
time.sleep(0.1)

def cli():
    return 0

if __name__ == '__main__':
    sys.exit(cli())
'''


test_quicken_program = '''
import os
import sys

from quicken import cli_factory

# Timeout with enough time to stay up for the server-reuse tests but go down
# for the startup tests.
@cli_factory(os.environ['TEST_SERVER_NAME'], server_idle_timeout=2)
def wrapper():
    import time
    # Represents imports and module initialization.
    time.sleep(0.1)
    def cli():
        return 0
    return cli

if __name__ == '__main__':
    sys.exit(wrapper())
'''


def run_code(code):
    args = [sys.executable, '-c', code]
    return subprocess.run(args).returncode


def test_python_spawn_time(benchmark):
    def target():
        return run_code('')

    result = benchmark(target)
    assert result == 0, 'Process must have exited cleanly'


def test_python_program_time(benchmark):
    def target():
        return run_code(test_program)

    result = benchmark(target)
    assert result == 0, 'Process must have exited cleanly'


def test_quicken_import_time(benchmark):
    def target():
        return run_code('from quicken import cli_factory')

    result = benchmark(target)
    assert result == 0, 'Process must have exited cleanly'


# Enforce uniqueness of directory between test runs.
test_id = uuid.uuid4()


def test_quicken_start_first_time(benchmark):
    # Use different server names to get server start time.
    test_name = current_test_name()
    i = 0
    def target():
        nonlocal i
        i += 1
        name = f'{test_name}-{test_id}-{i}'
        with env(TEST_SERVER_NAME=name):
            return run_code(test_quicken_program)

    result = benchmark(target)
    assert result == 0, 'Process must have exited cleanly'


def test_quicken_start_after_first_time(benchmark):
    test_name = current_test_name()
    name = f'{test_name}-{test_id}'
    def target():
        with env(TEST_SERVER_NAME=name):
            return run_code(test_quicken_program)

    assert target() == 0, 'Initial setup must exit cleanly'
    result = benchmark(target)
    assert result == 0, 'Process must have exited cleanly'

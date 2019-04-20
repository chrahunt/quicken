"""
Test that we don't break on Windows under import/normal use.
"""
import os

from . import cli_factory
from ..utils import isolated_filesystem
from ..utils.pytest import current_test_name, windows_only


pytestmark = windows_only


def test_function_is_not_run_using_server():
    # Same test as test_cli_wrapper.py:test_function_is_run_using_server,
    # but checking that it runs in-process.
    # Given a function decorated with cli_factory
    # And the server is not up
    # When the decorated function is executed
    # And the decorated function is executed again
    # Then it should be executed using the server
    # And it should be executed using the same server
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                output_file.write_text(
                    f'{os.getpid()} {os.getppid()}', encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        assert runner() == 0
        main_pid = str(os.getpid())
        runner_pid_1, parent_pid_1 = output_file.read_text(
            encoding='utf-8').strip().split()
        assert runner_pid_1 == main_pid
        #assert parent_pid_1 != runner_pid_1
        #assert parent_pid_1 != main_pid

        assert runner() == 0
        runner_pid_2, parent_pid_2 = output_file.read_text(
            encoding='utf-8').strip().split()
        assert runner_pid_2 == main_pid
        #assert parent_pid_2 != runner_pid_2
        #assert parent_pid_2 != main_pid
        #assert parent_pid_1 == parent_pid_2
        #assert runner_pid_1 != runner_pid_2

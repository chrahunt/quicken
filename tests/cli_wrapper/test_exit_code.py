import atexit
import os
import signal
import sys

import pytest

from . import cli_factory
from ..utils import captured_std_streams
from ..utils.process import contained_children
from ..utils.pytest import current_test_name, non_windows


pytestmark = non_windows


def test_exit_code_propagated_from_function():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            return 2

        return inner

    with contained_children():
        assert runner() == 2


def test_exit_code_propagated_on_sys_exit():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            sys.exit(3)

        return inner

    with contained_children():
        assert runner() == 3


def test_exit_code_propagated_on_sys_exit_0():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            sys.exit(0)

        return inner

    with contained_children():
        assert runner() == 0


def test_exit_code_propagated_on_sys_exit_none():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            sys.exit()

        return inner

    with contained_children():
        assert runner() == 0


def test_exit_code_propagated_on_os__exit():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            # noinspection PyProtectedMember
            os._exit(4)

        return inner

    with contained_children():
        assert runner() == 4


def test_exit_code_propagated_on_exception():
    message = "expected_exception"

    @cli_factory(current_test_name())
    def runner():
        def inner():
            raise RuntimeError(message)

        return inner

    with contained_children():
        with captured_std_streams() as (_stdin, _stdout, stderr):
            assert runner() == 1

        assert message in stderr.read()


def test_exit_code_propagated_on_atexit_sys_exit():
    # sys.exit has no effect when invoked from an atexit handler.
    # Note that this is not really the *expected* behavior based on the
    # documentation, see issue https://bugs.python.org/issue27035 for proposed
    # changes.
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
                sys.exit(5)

            atexit.register(func)

        return inner

    with contained_children():
        assert runner() == 0


@pytest.mark.xfail(reason="multiprocessing does not support atexit handlers")
def test_exit_code_propagated_on_atexit_exception():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
                raise RuntimeError("expected")

            atexit.register(func)

        return inner

    with contained_children():
        assert runner() == 1


@pytest.mark.xfail(reason="multiprocessing does not support atexit handlers")
def test_exit_code_propagated_on_atexit_os__exit():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
                # noinspection PyProtectedMember
                os._exit(3)

            atexit.register(func)

        return inner

    with contained_children():
        assert runner() == 3


def test_exit_code_propagated_when_server_gets_sigterm():
    # Given the server is running
    # And is processing a request
    # When the server receives sigterm
    # Then it will finish processing the request
    # And send the correct exit code
    @cli_factory(current_test_name())
    def runner():
        def inner():
            os.kill(os.getppid(), signal.SIGTERM)
            sys.exit(5)

        return inner

    with contained_children():
        assert runner() == 5

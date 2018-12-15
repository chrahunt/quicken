import logging
import os

from quicken import cli_factory

from .utils import contained_children, isolated_filesystem, setup_logging


setup_logging()


logger = logging.getLogger(__name__)


def test_function_is_run_using_daemon():
    # Given a function decorated with cli_factory
    # And daemon is not up
    # When the decorated function is executed
    # Then it should be executed using the daemon
    with isolated_filesystem() as path:
        @cli_factory('test_function_is_run_using_daemon')
        def runner():
            def inner():
                output_file.write_text(str(os.getpid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            logger.debug('Before runner')
            assert runner() == 0
            logger.debug('After runner')
            current_pid = str(os.getpid())
            test_pid = output_file.read_text(encoding='utf-8')
            assert test_pid.strip() != current_pid
            assert test_pid.strip() != ''


def test_daemon_reload_ok_when_daemon_not_up():
    # Given a pid_file path that points to an existing file
    # And daemon is not up
    # And daemon_reload parameter is True
    # When the decorated function is executed
    # Then the pidfile should be removed and the command should proceed via the
    #  daemon
    ...


def test_daemon_reload_ok_when_pidfile_missing():
    # Given a pid_file path pointing to a nonexistent file
    # And daemon is not up
    # And daemon_reload parameter is True
    # When the decorated function is executed
    # Then the daemon should be started successfully
    # And the daemon should process the command
    ...


def test_daemon_bypass_ok():
    # Given the daemon_bypass decorator parameter is True
    # And the daemon is up
    # When the decorated function is executed
    # Then the daemon should not receive the command
    # And the command should be processed
    ...


def test_log_file_unwritable_fails_fast():
    # Given a log_file path pointing to a location that is not writable
    # And the daemon is not up
    # When the decorated function is executed
    # Then an exception should be raised in the parent
    # And the daemon must not be up
    ...


def test_leftover_pid_file_is_ok():
    # Given a pid_file that exists and has no existing lock by a
    #  process
    # And the daemon is not up
    # When the decorated function is executed
    # Then the daemon should be started successfully
    # And the daemon should process the command
    ...


def test_leftover_socket_file_is_ok():
    # Given a socket_file that exists (and is a socket file)
    # And the daemon is not up
    # When the decorated function is executed
    # Then the daemon should be started successfully
    # And the daemon should process the command
    ...


def test_daemon_start_failed_raises_exception():
    # Given a socket_file path pointing to a location that is not writable
    # And the daemon is not up
    # When the decorated function is executed
    # Then the daemon should fail to come up
    # And an exception should be raised
    ...


def test_command_unhandled_exception_returns_nonzero():
    # Given a command that raises an unhandled exception
    # And the daemon is up
    # When the decorated function is executed
    # Then the daemon should process the command
    # And return a non-zero exit code
    # And the exception should be visible in the log output
    ...


def test_daemon_not_creating_pid_file_raises_exception():
    # Given the daemon is not up
    # And the daemon has been stubbed out to not create the pid file
    # When the decorated function is executed
    # Then it should time out waiting for the pid file to be created and raise
    #  an exception
    ...


def test_daemon_not_creating_socket_file_raises_exception():
    # Given the daemon is not up
    # And the daemon has been stubbed out to not create the socket file
    # When the decorated function is executed
    # Then it should time out waiting for the socket file to be created and
    #  raise an exception
    ...


def test_client_receiving_signals_forwards_to_daemon():
    # Given the daemon is processing a command
    # And the client receives one of:
    # - SIGINT
    # - SIGQUIT
    # - SIGSTOP
    # - SIGTERM
    # Then the same signal should be sent to the process running the command
    ...


def test_daemon_runner_inherits_environment():
    # Given a command that depends on an environment variable TEST
    # And the daemon is up and has an inherited environment value TEST=1
    # And the client is executed with TEST=2 in its environment
    # When the decorated function is executed
    # Then the command should see TEST=2 in its environment
    ...


def test_daemon_runner_inherits_cwd():
    ...


def test_daemon_runner_inherits_umask():
    ...


def test_daemon_runner_fails_when_communicating_to_stopped_server():
    # Given a daemon that is running but has received SIGSTOP
    # When the decorated function is executed.
    # Then the client should not hang, and should time out or throw a connection
    #  refused error.
    ...


def test_daemon_uses_runtime_path():
    # Given a function decorated with `runtime_path` provided as an argument.
    #
    # When the decorated function is executed.
    ...

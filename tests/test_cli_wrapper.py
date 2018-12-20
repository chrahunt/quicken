import json
import logging
from multiprocessing import Process, Pipe
import os
import sys
import time

from quicken import cli_factory
from quicken.constants import pid_file_name
from quicken.xdg import RuntimeDir

from .utils import (
    argv, contained_children, current_test_name, env, isolated_filesystem,
    setup_logging)


setup_logging()


logger = logging.getLogger(__name__)


def test_function_is_run_using_daemon():
    # Given a function decorated with cli_factory
    # And daemon is not up
    # When the decorated function is executed
    # And the decorated function is executed again
    # Then it should be executed using the daemon
    # And it should be executed using the same daemon
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                output_file.write_text(f'{os.getpid()} {os.getppid()}', encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            assert runner() == 0
            main_pid = str(os.getpid())
            runner_pid_1, parent_pid_1 = output_file.read_text(
                encoding='utf-8').strip().split()
            assert runner_pid_1 != main_pid
            assert parent_pid_1 != runner_pid_1
            assert parent_pid_1 != main_pid

            assert runner() == 0
            runner_pid_2, parent_pid_2 = output_file.read_text(
                encoding='utf-8').strip().split()
            assert runner_pid_2 != main_pid
            assert parent_pid_2 != runner_pid_2
            assert parent_pid_2 != main_pid
            assert parent_pid_1 == parent_pid_2
            assert runner_pid_1 != runner_pid_2


def test_daemon_runner_inherits_std_streams():
    ...


def test_daemon_runner_inherits_environment():
    # Given a command that depends on an environment variable TEST
    # And the daemon is up and has an inherited environment value TEST=1
    # And the client is executed with TEST=2 in its environment
    # When the decorated function is executed
    # Then the command should see TEST=2 in its environment
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                text = f"{os.environ['TEST']} {os.getppid()}"
                output_file.write_text(text, encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            main_pid = str(os.getpid())
            with env(TEST='1'):
                assert runner() == 0
                value, parent_pid_1 = output_file.read_text(
                    encoding='utf-8').strip().split()
                assert parent_pid_1 != main_pid
                assert value == '1'

            with env(TEST='2'):
                assert runner() == 0
                value, parent_pid_2 = output_file.read_text(
                    encoding='utf-8').strip().split()
                assert parent_pid_2 != main_pid
                assert parent_pid_1 == parent_pid_2
                assert value == '2'


def test_daemon_runner_inherits_args():
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                text = json.dumps([os.getppid(), *sys.argv])
                output_file.write_text(text, encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            main_pid = str(os.getpid())
            args = ['1', '2', '3']
            with argv(args):
                assert runner() == 0
                value = json.loads(
                    output_file.read_text(encoding='utf-8'))
                parent_pid_1 = value.pop(0)
                assert parent_pid_1 != main_pid
                assert value == args

            args = ['a', 'b', 'c']
            with argv(args):
                assert runner() == 0
                value = json.loads(
                    output_file.read_text(encoding='utf-8'))
                parent_pid_2 = value.pop(0)
                assert parent_pid_2 != main_pid
                assert parent_pid_1 == parent_pid_2
                assert value == args


def test_daemon_runner_inherits_cwd():
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                text = f"{os.getcwd()} {os.getppid()}"
                output_file.write_text(text, encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            main_pid = str(os.getpid())
            with isolated_filesystem() as path_1:
                assert runner() == 0
                cwd, parent_pid_1 = output_file.read_text(
                    encoding='utf-8').strip().split()
                assert parent_pid_1 != main_pid
                assert cwd == str(path_1)

            with isolated_filesystem() as path_2:
                assert runner() == 0
                cwd, parent_pid_2 = output_file.read_text(
                    encoding='utf-8').strip().split()
                assert parent_pid_2 != main_pid
                assert parent_pid_1 == parent_pid_2
                assert cwd == str(path_2)
                assert path_1 != path_2


def test_daemon_runner_inherits_umask():
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


def test_server_idle_timeout_is_respected():
    # Given the decorated function is initialized with an idle timeout of 100ms
    # When the decorated function is invoked
    # And again after 50ms
    # And again after 50ms
    # Then each request should be handled by the same server
    # And the server should shut down 100ms after the last request
    with isolated_filesystem() as path:
        idle_timeout = 0.1
        @cli_factory(
            current_test_name(), runtime_dir_path=path,
            server_idle_timeout=idle_timeout)
        def runner():
            def inner():
                text = str(os.getppid())
                output_file.write_text(text, encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            main_pid = str(os.getpid())
            assert runner() == 0
            first_parent_pid = output_file.read_text(encoding='utf-8')
            for _i in range(3):
                time.sleep(0.05)
                assert runner() == 0
                parent_pid = output_file.read_text(encoding='utf-8')
                assert parent_pid == first_parent_pid
                assert parent_pid != main_pid
            time.sleep(idle_timeout * 2)
            assert runner() == 0
            parent_pid = output_file.read_text(encoding='utf-8')
            assert parent_pid != first_parent_pid
            assert parent_pid != main_pid


def test_server_idle_timeout_acknowledges_active_children():
    # Given the decorated function is initialized with an idle timeout of 100ms
    # And the decorated function takes 200ms to execute
    # When the decorated function is invoked
    # And again after 250ms
    # Then both requests should be handled by the same server
    # And the server should shut down 100ms after the last request
    with isolated_filesystem() as path:
        idle_timeout = 0.1
        @cli_factory(
            current_test_name(), runtime_dir_path=path,
            server_idle_timeout=idle_timeout)
        def runner():
            def inner():
                text = str(time.time())
                time.sleep(idle_timeout * 2)
                text += f' {os.getppid()}'
                output_file.write_text(text, encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            main_pid = str(os.getpid())
            assert runner() == 0
            t, first_parent_pid = output_file.read_text(encoding='utf-8').split()
            assert first_parent_pid != main_pid
            now = time.time()
            # Within 10% of the idle timeout.
            assert idle_timeout * 2 - (now - float(t)) < 0.1 * idle_timeout
            time.sleep(idle_timeout * 0.5)
            assert runner() == 0
            _, parent_pid = output_file.read_text(encoding='utf-8').split()
            assert parent_pid != main_pid
            assert parent_pid == first_parent_pid


def test_stale_pid_file_is_ok():
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


def test_daemon_reload_ok():
    # Given the decorated function has been executed
    # And the daemon is up
    # When the decorated function is executed again
    # And the function passed to the daemon_reload parameter returns True
    # Then the daemon will be restarted
    # And the decorated function should be executed in process with a new parent
    with isolated_filesystem() as path:
        def daemon_reload():
            return os.environ.get('TEST_RELOAD') is not None

        @cli_factory(
            current_test_name(), reload_daemon=daemon_reload,
            runtime_dir_path=path)
        def runner():
            def inner():
                # Get parent pid, for comparison.
                output_file.write_text(str(os.getppid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            assert runner() == 0
            main_pid = str(os.getpid())
            parent_pid_1 = output_file.read_text(encoding='utf-8').strip()
            assert parent_pid_1
            assert parent_pid_1 != main_pid
            with env(TEST_RELOAD='1'):
                assert runner() == 0
                parent_pid_2 = output_file.read_text(encoding='utf-8').strip()
                assert parent_pid_2
                assert parent_pid_2 != main_pid
                assert parent_pid_1 != parent_pid_2


def test_daemon_reload_ok_when_daemon_not_up():
    # Given the daemon is not up
    # And function passed to the reload_daemon parameter returns True
    # When the decorated function is executed
    # Then it should be executed as expected
    with isolated_filesystem() as path:
        def reload_daemon():
            return True

        @cli_factory(
            current_test_name(), reload_daemon=reload_daemon,
            runtime_dir_path=path)
        def runner():
            def inner():
                # Get parent pid, for comparison.
                output_file.write_text(str(os.getpid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            assert runner() == 0
            main_pid = str(os.getpid())
            test_pid = output_file.read_text(encoding='utf-8').strip()
            assert test_pid
            assert test_pid != main_pid


def test_daemon_reload_ok_when_stale_pidfile_exists():
    # Given a stale pid file exists in the runtime directory
    # And a daemon is not up (i.e. no lock on pid file)
    # And the function passed to the daemon_reload parameter returns True
    # When the decorated function is executed
    # Then the contained pid should not be killed
    # And the function should be executed as expected
    with isolated_filesystem() as path:

        def worker(conn):
            # Close when indicated by parent.
            conn.recv()
            conn.close()

        def daemon_reload():
            return True

        @cli_factory(
            current_test_name(), reload_daemon=daemon_reload,
            runtime_dir_path=path)
        def runner():
            def inner():
                # Get parent pid, for comparison.
                output_file.write_text(str(os.getppid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            # Set up other process.
            parent_conn, child_conn = Pipe()
            p = Process(target=worker, args=(child_conn,))
            p.start()
            worker_pid = str(p.pid)
            runtime_dir = RuntimeDir(dir_path=path)
            pid_file = runtime_dir.path(pid_file_name)
            pid_file.write_text(worker_pid, encoding='utf-8')

            assert runner() == 0
            # Make sure the same pid file was used for the daemon.
            main_pid = str(os.getpid())
            runner_parent_pid = output_file.read_text(encoding='utf-8')
            assert runner_parent_pid.strip() != main_pid
            assert runner_parent_pid.strip() != ''
            daemon_pid = pid_file.read_text(encoding='utf-8').strip()
            assert daemon_pid == runner_parent_pid
            assert worker_pid != daemon_pid
            parent_conn.send(1)
            p.join()
            # Must not have been killed.
            assert p.exitcode == 0


def test_daemon_bypass_ok():
    # Given the daemon_bypass decorator parameter is True
    # And the daemon is up
    # When the decorated function is executed
    # Then the daemon should not receive the command
    # And the command should be processed
    with isolated_filesystem() as path:
        def bypass_daemon():
            return True

        @cli_factory(
            current_test_name(), bypass_daemon=bypass_daemon,
            runtime_dir_path=path)
        def runner():
            def inner():
                # Get parent pid, for comparison.
                output_file.write_text(str(os.getpid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            assert runner() == 0
            main_pid = str(os.getpid())
            runner_pid = output_file.read_text(encoding='utf-8')
            assert runner_pid.strip() == main_pid


def test_log_file_unwritable_fails_fast():
    # Given a log_file path pointing to a location that is not writable
    # And the daemon is not up
    # When the decorated function is executed
    # Then an exception should be raised in the parent
    # And the daemon must not be up
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


def test_daemon_runner_fails_when_communicating_to_stopped_server():
    # Given a daemon that is running but has received SIGSTOP
    # When the decorated function is executed.
    # Then the client should not hang, and should time out or throw a connection
    #  refused error.
    ...

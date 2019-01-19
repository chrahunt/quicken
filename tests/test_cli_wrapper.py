import atexit
import json
import logging
from multiprocessing import active_children, Process, Pipe
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time

import psutil
import pytest
from pytest_mock import mocker

from quicken import __version__, cli_factory as _cli_factory, QuickenError
from quicken._constants import server_state_name, socket_name
from quicken._xdg import RuntimeDir

from .utils import (
    argv, contained_children, current_test_name, env, isolated_filesystem,
    umask)


logger = logging.getLogger(__name__)


def cli_factory(*args, **kwargs):
    log_file = Path('logs').absolute() / f'{current_test_name()}-server.log'
    kwargs['log_file'] = log_file
    return _cli_factory(*args, **kwargs)


def test_function_is_run_using_server():
    # Given a function decorated with cli_factory
    # And server is not up
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


def test_runner_inherits_std_streams():
    ...


def test_runner_inherits_environment():
    # Given a command that depends on an environment variable TEST
    # And the server is up and has an inherited environment value TEST=1
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


def test_runner_inherits_args():
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


def test_runner_inherits_cwd():
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


def test_runner_inherits_umask():
    # Given the server is processing a command.
    # And the client has a umask of 077
    # When the runner process creates files
    # Then they should have permission 700
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
        def runner():
            def inner():
                output_path.touch(0o777)
            return inner

        output_path = path / 'output.txt'

        with contained_children():
            with umask(0o077):
                assert runner() == 0
            result = output_path.stat()
            user_rwx = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
            assert stat.S_IMODE(result.st_mode) == user_rwx


@pytest.mark.skip(reason='signals not currently forwarded')
def test_client_receiving_signals_forwards_to_runner():
    # Given the server is processing a command in a subprocess.
    # And the client a signal (except SIGSTOP/SIGKILL)
    # Then the same signal should be sent to the subprocess running the command
    pid = os.getpid()
    all_signals = set(
        getattr(signal, s)
        for s in dir(signal) if s.startswith('SIG') and '_' not in s)
    test_signals = all_signals - {signal.SIGSTOP, signal.SIGKILL}

    # For convenience we get the process to send signals to the client which
    # should them send them back to the process.
    @cli_factory(current_test_name())
    def runner():
        def inner():
            signal.pthread_sigmask(signal.SIG_BLOCK, range(1, signal.NSIG))
            # Set handler for all signals otherwise they do not get added to
            # set, per sigpending(2).
            for sig in test_signals:
                signal.signal(sig, lambda _num, _frame: ...)
                os.kill(pid, sig)

            received_signals = signal.sigpending()
            output_path.write_text(
                ','.join(sorted(received_signals)), encoding='utf-8')

        return inner

    with isolated_filesystem() as path:
        output_path = Path(path) / 'output.txt'
        assert runner() == 0
        traced_signals = output_path.read_text(encoding='utf-8')
        assert traced_signals == ','.join(sorted(test_signals))


def test_killed_client_causes_handler_to_exit():
    # Given the server is processing a command in a subprocess.
    # And the client process is killed (receives SIGKILL and exits)
    # Then the same signal should be sent to the subprocess running the command
    # And it should exit
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
            assert runner() == 0
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


def test_leftover_socket_file_is_ok():
    # Given a socket_file that exists (and is a socket file)
    # And the server is not up
    # When the decorated function is executed
    # Then the server should be started successfully
    # And the server should process the command
    with isolated_filesystem() as path:
        @cli_factory(current_test_name(), runtime_dir_path=path)
        def runner():
            def inner():
                pass
            return inner

        (path / socket_name).touch()

        with contained_children():
            assert runner() == 0


def test_server_reload_ok():
    # Given the decorated function has been executed
    # And the server is up
    # When the decorated function is executed again
    # And the function passed to the server_reload parameter returns True
    # Then the server will be restarted
    # And the decorated function should be executed in process with a new parent
    with isolated_filesystem() as path:
        def sometimes_reload():
            return os.environ.get('TEST_RELOAD') is not None

        @cli_factory(
            current_test_name(), reload_server=sometimes_reload,
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


def test_server_reload_ok_when_server_not_up():
    # Given the server is not up
    # And function passed to the reload_server parameter returns True
    # When the decorated function is executed
    # Then it should be executed as expected
    with isolated_filesystem() as path:
        def always_reload():
            return True

        @cli_factory(
            current_test_name(), reload_server=always_reload,
            runtime_dir_path=path)
        def runner():
            def inner():
                output_file.write_text(str(os.getpid()), encoding='utf-8')
                return 0
            return inner

        output_file = path / 'test.txt'

        with contained_children():
            assert runner() == 0
            main_pid = str(os.getpid())
            test_pid = output_file.read_text(encoding='utf-8')
            assert test_pid
            assert test_pid != main_pid


def test_server_reload_ok_when_stale_pidfile_exists():
    # Given a stale pid file exists in the runtime directory
    # And a server is not up (i.e. no lock on pid file)
    # And the function passed to the reload_server parameter returns True
    # When the decorated function is executed
    # Then the contained pid should not be killed
    # And the function should be executed as expected
    with isolated_filesystem() as path:

        def worker(conn):
            # Close when indicated by parent.
            conn.recv()
            conn.close()

        def always_reload():
            return True

        @cli_factory(
            current_test_name(), reload_server=always_reload,
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
            process = psutil.Process(pid=p.pid)
            state_file = runtime_dir.path(server_state_name)
            state_file.write_text(json.dumps({
                'create_time': process.create_time(),
                'pid': p.pid,
                'version': __version__,
            }), encoding='utf-8')

            assert runner() == 0
            # Make sure the same pid file was used for the server.
            main_pid = str(os.getpid())
            runner_parent_pid = output_file.read_text(encoding='utf-8')
            assert runner_parent_pid.strip() != main_pid
            assert runner_parent_pid.strip() != ''
            server_info = json.loads(state_file.read_text(encoding='utf-8'))
            assert str(server_info['pid']) == runner_parent_pid
            assert worker_pid != str(server_info['pid'])
            parent_conn.send(1)
            p.join()
            # Must not have been killed.
            assert p.exitcode == 0


def test_server_bypass_ok():
    # Given the server_bypass decorator parameter is True
    # And the server is up
    # When the decorated function is executed
    # Then the server should not receive the command
    # And the command should be processed
    with isolated_filesystem() as path:
        def bypass_server():
            return True

        @cli_factory(
            current_test_name(), bypass_server=bypass_server,
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


@pytest.mark.xfail(reason='does not fail fast')
def test_log_file_unwritable_fails_fast():
    # Given a log_file path pointing to a location that is not writable
    # And the server is not up
    # When the decorated function is executed
    # Then an exception should be raised in the parent
    # And the server must not be up
    with isolated_filesystem() as path:
        log_path = Path(path) / 'log.txt'
        log_path.touch()

        @cli_factory(current_test_name(), log_file=str(log_path))
        def runner():
            def inner():
                pass
            return inner

        log_path.chmod(stat.S_IRUSR)

        with contained_children():
            with pytest.raises(QuickenError) as e:
                runner()

        assert str(log_path) in str(e)


@pytest.mark.xfail(reason='wrong error raised')
def test_unwritable_runtime_dir_raises_exception():
    # Given a runtime_dir path pointing to a location that is not writable
    # And the server is not up
    # When the decorated function is executed
    # Then the server should fail to come up
    # And an exception should be raised
    with isolated_filesystem() as path:
        @cli_factory(current_test_name(), runtime_dir_path=path)
        def runner():
            def inner():
                pass
            return inner

        Path(path).chmod(stat.S_IRUSR | stat.S_IWUSR)

        with contained_children():
            with pytest.raises(QuickenError) as e:
                runner()


@pytest.mark.xfail(reason='wrong error raised')
def test_unwritable_socket_file_raises_exception():
    # Given a runtime_dir path pointing to a directory with an unwritable
    #  'socket' file
    # And the server is not up
    # When the decorated function is executed
    # Then the server should fail to come up
    # And it should raise a QuickenError with message 'could not write socket
    #  file' and the path.
    with isolated_filesystem() as path:
        @cli_factory(current_test_name(), runtime_dir_path=path)
        def runner():
            def inner():
                pass
            return inner

        (Path(path) / socket_name).touch(mode=stat.S_IRUSR)

        with contained_children():
            with pytest.raises(QuickenError):
                runner()


@pytest.mark.xfail(reason='wrong error raised')
def test_server_not_creating_socket_file_raises_exception(mocker):
    # Given the server is not up
    # And the server has been stubbed out to not create the socket file
    # When the decorated function is executed
    # Then it should time out waiting for the socket file to be created and
    #  raise a QuickenError with message 'timed out connecting to server'
    mocker.patch('quicken._server.run')
    @cli_factory(current_test_name())
    def runner():
        def inner():
            pass
        return inner

    with contained_children():
        with pytest.raises(QuickenError):
            runner()


@pytest.mark.xfail(reason='wrong error raised')
def test_server_not_listening_on_socket_file_raises_exception(mocker):
    # Given the server is not up
    # And the server has been stubbed out to bind to the socket file but not
    #  listen
    # When the decorated function is executed
    # Then it should raise a QuickenError with message 'failed to connect to
    #  server'
    run_function = mocker.patch('quicken._server.run')

    def fake_listener(*_args, **_kwargs):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socket_name)

    run_function.side_effect = fake_listener
    with isolated_filesystem() as path:
        @cli_factory(current_test_name(), runtime_dir_path=path)
        def runner():
            def inner():
                pass
            return inner

        with contained_children():
            with pytest.raises(QuickenError) as e:
                runner()


@pytest.mark.skip(reason='client currently hangs')
def test_runner_fails_when_communicating_to_stopped_server():
    # Given a server that is running but has received SIGSTOP
    # When the decorated function is executed.
    # Then the client should not hang
    # And should raise a QuickenError with message 'failed to connect to server'
    with isolated_filesystem() as path:
        @cli_factory(current_test_name(), runtime_dir_path=path)
        def runner():
            def inner():
                output_file.write_text(message, encoding='utf-8')
            return inner

        message = 'hello world'
        output_file = Path(path) / 'output.txt'

        with contained_children():
            # Ensure server is running.
            assert runner() == 0
            text = output_file.read_text(encoding='utf-8')
            assert text == message

            server_state = json.loads(
                Path(server_state_name).read_text(encoding='utf-8'))
            os.kill(server_state['pid'], signal.SIGSTOP)
            with pytest.raises(QuickenError):
                runner()


def test_command_does_not_hang_on_first_invocation():
    # Given a function decorated with cli_factory
    # When the function is executed
    # Then multiprocessing.active_children will not have any members
    # And when the function is executed again it will have the same ppid
    with isolated_filesystem() as path:
        @cli_factory(current_test_name())
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
            parent_pid_1 = output_file.read_text(
                encoding='utf-8').strip().split()
            assert parent_pid_1 != main_pid
            assert not active_children(), 'No active children should be present'

            assert runner() == 0
            parent_pid_2 = output_file.read_text(
                encoding='utf-8').strip().split()
            assert parent_pid_2 != main_pid
            assert parent_pid_1 == parent_pid_2


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
            os._exit(4)
        return inner

    with contained_children():
        assert runner() == 4


def test_exit_code_propagated_on_exception():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            raise RuntimeError('expected')
        return inner

    with contained_children():
        assert runner() == 1


def test_exit_code_propagated_on_atexit_sys_exit():
    # sys.exit has no effect when invoked from an atexit handler.
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
                sys.exit(5)
            atexit.register(func)
        return inner

    with contained_children():
        assert runner() == 0


@pytest.mark.xfail(reason='multiprocessing does not support atexit handlers')
def test_exit_code_propagated_on_atexit_exception():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
                raise RuntimeError('expected')
            atexit.register(func)
        return inner

    with contained_children():
        assert runner() == 1


@pytest.mark.xfail(reason='multiprocessing does not support atexit handlers')
def test_exit_code_propagated_on_atexit_os__exit():
    @cli_factory(current_test_name())
    def runner():
        def inner():
            def func():
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

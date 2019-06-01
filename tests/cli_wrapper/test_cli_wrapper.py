import json
import logging
import os
import signal
import socket
import stat
import sys
import tempfile
import time

from multiprocessing import active_children, Process
from pathlib import Path
from unittest.mock import Mock, patch

import psutil
import pytest

import quicken.lib._lib

from quicken.lib import QuickenError
from quicken.lib._constants import server_state_name, socket_name
from quicken.lib._signal import forwarded_signals
from quicken.lib._xdg import RuntimeDir

from . import cli_factory
from ..utils import (
    argv, captured_std_streams, env, isolated_filesystem, kept, umask
)
from ..utils.path import get_bound_path
from ..utils.process import contained_children
from ..utils.pytest import non_windows
from ..utils.subprocess_helper import track_state
from ..utils.subprocess_helper.__test_helper__ import record
from ..utils.watch import wait_for_create


pytestmark = non_windows


logger = logging.getLogger(__name__)


def test_function_is_run_using_server():
    # Given a function decorated with cli_factory
    # And the server is not up
    # When the decorated function is executed
    # And the decorated function is executed again
    # Then it should be executed using the server
    # And it should be executed using the same server
    @cli_factory()
    def runner():
        def inner():
            record()

        return inner

    test_pid = str(os.getpid())

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != run1.pid
        assert run1.ppid != test_pid

        with track_state() as run2:
            assert runner() == 0

        assert run2.pid != test_pid
        assert run2.ppid != run2.pid
        assert run2.ppid != test_pid
        assert run1.ppid == run2.ppid
        assert run1.pid != run2.pid


def test_quicken_is_importing_is_set():
    # Given a function is decorated
    # When the main provider function is executed and main is executed
    # Then quicken.is_importing should be True during main provider execution
    # And quicken.is_importing should be False during main execution
    import quicken

    @cli_factory()
    def runner():
        assert quicken.is_importing

        def inner():
            assert not quicken.is_importing

        return inner

    assert not quicken.is_importing

    with contained_children():
        assert runner() == 0


def test_quicken_is_importing_not_set_when_bypassed():
    # Given a function is decorated
    # And bypass_server return True
    # When the main provider function is executed
    # Then quicken.is_importing should be False
    import quicken

    def bypass_server():
        return True

    @cli_factory(bypass_server=bypass_server)
    def runner():
        assert not quicken.is_importing

        def inner():
            assert not quicken.is_importing
            return 0

        return inner

    assert not quicken.is_importing

    with contained_children():
        assert runner() == 0


# This may time out if not all references to the std streams are closed in
# the server.
@pytest.mark.timeout(5)
def test_runner_inherits_std_streams():
    # Given the server is not up
    # And the standard streams have been overridden
    error_text = 'hello world'
    stdout_text = f'stdout : {error_text}'

    @cli_factory()
    def runner():
        def inner():
            content = sys.stdin.read()
            sys.stdout.write(stdout_text)
            raise RuntimeError(content)

        return inner

    with contained_children():
        with captured_std_streams() as (stdin, stdout, stderr):
            stdin.write(error_text)
            # Allow read in child to complete.
            stdin.close()
            assert runner() == 1
        stdout_output = stdout.read()
        stderr_output = stderr.read()
        assert error_text in stderr_output
        assert 'Traceback' in stderr_output
        assert stdout_output == stdout_text


def test_runner_inherits_environment():
    # Given a command that depends on an environment variable TEST
    # And the server is up and has an inherited environment value TEST=1
    # And the client is executed with TEST=2 in its environment
    # When the decorated function is executed
    # Then the command should see TEST=2 in its environment
    @cli_factory()
    def runner():
        def inner():
            record(env=os.environ['TEST'])

        return inner

    test_pid = os.getpid()

    with contained_children():
        with env(TEST='1'):
            with track_state() as run1:
                assert runner() == 0

            assert run1.env == '1'
            assert run1.pid != test_pid

        with env(TEST='2'):
            with track_state() as run2:
                assert runner() == 0

            assert run2.env == '2'
            assert run2.pid != test_pid
            assert run1.ppid == run2.ppid


def test_runner_inherits_args():
    @cli_factory()
    def runner():
        def inner():
            record(args=sys.argv)

        return inner

    test_pid = os.getpid()

    with contained_children():
        args = ['1', '2', '3']
        with argv(args):
            with track_state() as run1:
                assert runner() == 0

            assert run1.args == args
            assert run1.pid != test_pid
            assert run1.ppid != test_pid

        args = ['a', 'b', 'c']
        with argv(args):
            with track_state() as run2:
                assert runner() == 0

            assert run2.args == args
            assert run1.pid != run2.pid
            assert run1.ppid == run2.ppid


def test_runner_inherits_cwd():
    @cli_factory()
    def runner():
        def inner():
            record(cwd=os.getcwd())

        return inner

    test_pid = os.getpid()

    with contained_children():
        with isolated_filesystem() as path_1:
            with track_state() as run1:
                assert runner() == 0

            assert run1.cwd == str(path_1)
            assert run1.pid != test_pid
            assert run1.ppid != test_pid

        with isolated_filesystem() as path_2:
            with track_state() as run2:
                assert runner() == 0

            assert run2.cwd == str(path_2)
            assert run1.pid != run2.pid
            assert run1.ppid == run2.ppid


def test_runner_inherits_umask():
    # Given the server is processing a command.
    # And the client has a umask of 077
    # When the runner process creates files
    # Then they should have permission 700
    with isolated_filesystem() as path:
        @cli_factory()
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


@pytest.mark.timeout(5)
def test_client_receiving_signals_forwards_to_runner():
    # Given the server is processing a command in a subprocess.
    # And the client receives a basic signal (i.e. any except SIGSTOP, SIGKILL,
    #  SIGT*)
    # Then the same signal should be sent to the subprocess running the command

    # Ensure that the test runner caller doesn't impact signal handling.
    signal.pthread_sigmask(signal.SIG_SETMASK, [])

    pid = os.getpid()

    def to_string(signals):
        return ','.join(
            str(int(s)) for s in sorted(signals))

    # Omit SIGT* to avoid stopping the test.
    test_signals = forwarded_signals - {
        signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU
    }

    # For convenience we get the process itself to send signals to the client
    # which then sends them back to the process.
    @cli_factory()
    def runner():
        def inner():
            # Block signals unconditionally to avoid them impacting our return
            # code.
            signal.pthread_sigmask(signal.SIG_SETMASK, test_signals)
            for sig in test_signals:
                os.kill(pid, sig)

            received_signal = False
            received_signals = signal.sigpending()
            while received_signals != test_signals:
                result = signal.sigtimedwait(forwarded_signals, 0.1)
                if result is not None:
                    received_signal = True
                    received_signals.add(result.si_signo)
                elif received_signal:
                    # Only trace after the first empty response.
                    received_signal = False
                    logger.debug(
                        'Waiting for %s', test_signals - received_signals)

            output_path.write_text(
                to_string(received_signals), encoding='utf-8')

        return inner

    with isolated_filesystem() as path:
        output_path = Path(path) / 'output.txt'

        with contained_children():
            assert runner() == 0
            traced_signals = output_path.read_text(encoding='utf-8')
            assert traced_signals == to_string(test_signals)


@pytest.mark.timeout(5)
def test_client_receiving_tstp_ttin_stops_itself():
    # Given the server is processing a command in a subprocess
    # When the client receives signal.SIGTSTP or signal.SIGTTIN
    # Then the same signal should be sent to the subprocess running the command
    # And the client should be stopped

    # Ensure that the test runner caller doesn't impact signal handling.
    signal.pthread_sigmask(signal.SIG_SETMASK, [])

    test_signals = {signal.SIGTSTP, signal.SIGTTIN}
    resume_signal = signal.SIGUSR1

    @cli_factory()
    def runner():
        def inner():
            # Block signals we expect to receive
            signal.pthread_sigmask(signal.SIG_BLOCK, test_signals | {signal.SIGUSR1})
            # Save our pid so it is accessible to the test process, avoiding
            # any race conditions where the file may be empty.
            pid = os.getpid()
            fd, path = tempfile.mkstemp()
            os.write(fd, str(pid).encode('utf-8'))
            os.fsync(fd)
            os.close(fd)
            os.rename(path, runner_pid_file)

            for sig in test_signals:
                logger.debug('Waiting for %s', sig)
                signal.sigwait({sig})
                # Stop self to indicate success to test process.
                os.kill(pid, signal.SIGSTOP)

            logger.debug('Waiting for signal to exit')
            # This is required otherwise we may exit while the test is checking
            # for our status.
            signal.sigwait({resume_signal})
            logger.debug('All signals received')

        return inner

    def client():
        # All the work to forward signals is done in the library.
        sys.exit(runner())

    def wait_for(predicate):
        # Busy wait since we don't have a good way to get signalled on process
        # status change.
        while not predicate():
            time.sleep(0.1)

    with isolated_filesystem() as path:
        with contained_children():
            # Get process pids. The Process object already has the client pid,
            # but we need to wait for the runner pid to be written to the file.
            runner_pid_file = Path('runner_pid').absolute()
            runtime_dir = RuntimeDir(dir_path=str(path))
            p = Process(target=client)
            p.start()
            assert wait_for_create(
                get_bound_path(runtime_dir, runner_pid_file.name), timeout=2), \
                f'{runner_pid_file} must have been created'
            runner_pid = int(runner_pid_file.read_text(encoding='utf-8'))

            # Stop and continue the client process, checking that it was
            # correctly applied to both the client and runner processes.
            client_process = psutil.Process(pid=p.pid)
            runner_process = psutil.Process(pid=runner_pid)
            for sig in [signal.SIGTSTP, signal.SIGTTIN]:
                logger.debug('Sending %s', sig)
                client_process.send_signal(sig)
                logger.debug('Waiting for client to stop')
                wait_for(
                    lambda: client_process.status() == psutil.STATUS_STOPPED)
                logger.debug('Waiting for runner to stop')
                wait_for(
                    lambda: runner_process.status() == psutil.STATUS_STOPPED)

                client_process.send_signal(signal.SIGCONT)
                logger.debug('Waiting for client to resume')
                wait_for(
                    lambda: client_process.status() != psutil.STATUS_STOPPED)
                logger.debug('Waiting for runner to resume')
                wait_for(
                    lambda: runner_process.status() != psutil.STATUS_STOPPED)

            # Resume runner process so it exits.
            runner_process.send_signal(resume_signal)

            logger.debug('Waiting for client to finish')
            p.join()
            assert p.exitcode == 0


@pytest.mark.timeout(5)
def test_killed_client_causes_handler_to_exit():
    # Given the server is processing a command in a subprocess.
    # And the client process is killed (receives SIGKILL and exits)
    # Then the same signal should be sent to the subprocess running the command
    # And it should exit.
    @cli_factory()
    def runner():
        def inner():
            # Block just to ensure that only an unblockable signal would
            # be able terminate the process.
            signal.pthread_sigmask(signal.SIG_BLOCK, forwarded_signals)
            pid = os.getpid()
            fd, path = tempfile.mkstemp()
            os.write(fd, str(pid).encode('utf-8'))
            os.fsync(fd)
            os.close(fd)
            os.rename(path, runner_pid_file)
            logger.debug('Inner function waiting')
            while True:
                signal.pause()

        return inner

    # Client runs in child process so we don't kill the test process itself.
    def client():
        logger.debug('Client starting')
        signal.pthread_sigmask(signal.SIG_BLOCK, forwarded_signals)
        sys.exit(runner())

    with isolated_filesystem() as path:
        with contained_children():
            runner_pid_file = Path('runner_pid').absolute()
            runtime_dir = RuntimeDir(dir_path=str(path))
            p = Process(target=client)
            logger.debug('Starting process')
            p.start()

            logger.debug('Waiting for pid file')
            assert wait_for_create(
                get_bound_path(runtime_dir, runner_pid_file.name), timeout=2), \
                f'{runner_pid_file} must have been created'
            runner_pid = int(runner_pid_file.read_text(encoding='utf-8'))
            logger.debug('Runner started with pid: %d', runner_pid)

            client_process = psutil.Process(pid=p.pid)
            runner_process = psutil.Process(pid=runner_pid)

            logger.debug('Killing client')
            client_process.kill()
            logger.debug('Waiting for client')
            p.join()
            logger.debug('Waiting for runner')
            runner_process.wait()


def test_server_idle_timeout_is_respected():
    # Given the decorated function is initialized with an idle timeout of 100ms
    # When the decorated function is invoked
    # And again after 50ms
    # And again after 50ms
    # Then each request should be handled by the same server
    # And the server should shut down 100ms after the last request
    idle_timeout = 0.1

    @cli_factory(server_idle_timeout=idle_timeout)
    def runner():
        def inner():
            record()

        return inner

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        for _i in range(3):
            time.sleep(0.05)
            with track_state() as run_i:
                assert runner() == 0

            assert run_i.pid != run1.pid
            assert run_i.ppid == run1.ppid

        time.sleep(idle_timeout * 2)

        with track_state() as run2:
            assert runner() == 0

        assert run2.pid != test_pid
        assert run2.ppid != test_pid
        assert run1.ppid != run2.ppid


def test_server_idle_timeout_acknowledges_active_children():
    # Given the decorated function is initialized with an idle timeout of 100ms
    # And the decorated function takes 200ms to execute
    # When the decorated function is invoked
    # And again after 250ms
    # Then both requests should be handled by the same server
    # And the server should shut down 100ms after the last request
    idle_timeout = 0.1

    @cli_factory(server_idle_timeout=idle_timeout)
    def runner():
        def inner():
            t = time.time()
            time.sleep(idle_timeout * 2)
            record(time=t)

        return inner

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        now = time.time()
        # Within 10% of the idle timeout.
        assert idle_timeout * 2 - (now - run1.time) < 0.1 * idle_timeout
        time.sleep(idle_timeout * 0.5)

        with track_state() as run2:
            assert runner() == 0

        assert run2.pid != test_pid
        assert run1.ppid == run2.ppid


def test_leftover_socket_file_is_ok():
    # Given a socket_file that exists (and is a socket file)
    # And the server is not up
    # When the decorated function is executed
    # Then the server should be started successfully
    # And the server should process the command
    with isolated_filesystem() as path:
        @cli_factory(runtime_dir_path=path)
        def runner():
            def inner():
                pass

            return inner

        (path / socket_name).touch()

        with contained_children():
            assert runner() == 0


def test_user_data_saved_by_server():
    # Given user_data provided to the function decorator
    # When the decorated function is executed
    # Then the server should write the user data to the user_data
    #  field of the metadata
    with isolated_filesystem() as path:
        user_data = {
            'hello': 1,
        }

        @cli_factory(runtime_dir_path=path, user_data=user_data)
        def runner():
            def inner():
                pass

            return inner

        with contained_children():
            assert runner() == 0
            text = (path / server_state_name).read_text(encoding='utf-8')
            obj = json.loads(text)
            assert user_data == obj['user_data']


def test_user_data_provided_to_reload():
    # Given user_data provided to the function decorator
    # And the decorated function has been executed
    # And the server is up
    # When the decorated function is executed
    # Then the reload_server function will be provided the previously-provided
    #  user_data

    reload_handler = Mock()

    user_data_1 = {
        'hello': 1,
    }

    @cli_factory(user_data=user_data_1, reload_server=reload_handler)
    def runner_1():
        def inner():
            pass

        return inner

    user_data_2 = {
        'hello': 2,
    }

    @cli_factory(user_data=user_data_2, reload_server=reload_handler)
    def runner_2():
        def inner():
            pass
        return inner

    with contained_children():
        assert runner_1() == 0
        reload_handler.assert_not_called()
        assert runner_2() == 0
        reload_handler.assert_called_with(user_data_1, user_data_2)


def test_user_data_invalid_raises_exception():
    # Given user_data that is not JSON serializable
    # When the decorated function is executed
    # Then a QuickenError will be raised
    class BadData:
        pass

    @cli_factory(user_data=BadData())
    def runner():
        ...

    with pytest.raises(QuickenError) as e:
        runner()

    assert 'user_data' in str(e)


def test_server_reload_ok():
    # Given the decorated function has been executed
    # And the server is up
    # When the decorated function is executed again
    # And the function passed to the server_reload parameter returns True
    # Then the server will be restarted
    # And the decorated function should be executed in process with a new parent
    def sometimes_reload(*_):
        return os.environ.get('TEST_RELOAD') is not None

    @cli_factory(reload_server=sometimes_reload)
    def runner():
        def inner():
            record()

        return inner

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        with env(TEST_RELOAD='1'):
            with track_state() as run2:
                assert runner() == 0

            assert run1.ppid != run2.ppid
            assert run1.pid != run2.pid
            assert run1.pid != test_pid
            assert run1.ppid != test_pid


def test_server_reload_not_called_when_server_not_up():
    # Given the server is not up
    # And a function passed to the reload_server parameter
    # When the decorated function is executed
    # Then the reload_server function should not be executed
    reload_handler = Mock()

    @cli_factory(reload_server=reload_handler)
    def runner():
        def inner():
            record()

        return inner

    test_pid = str(os.getpid())

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        reload_handler.assert_not_called()

        assert run1.pid != test_pid
        assert run1.ppid != test_pid


def test_server_reload_when_library_version_changes():
    # Given the server is up when the library was at x.y.z
    # And the library is updated, and quicken.__version__ is now x.y.(z + 1)
    # When the decorated function is executed
    # Then the server should be reloaded
    # And the new function should be executed under a different ppid.
    @cli_factory()
    def runner():
        def inner():
            record()

        return inner

    import quicken

    def increment_patch(version: str):
        x, y, z = version.split('.')
        return '.'.join([x, y, str(int(z) + 1)])

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        with kept(quicken.lib._lib, '__version__'):
            # Set on _lib since the attribute is imported directly.
            quicken.lib._lib.__version__ = increment_patch(
                quicken.lib._lib.__version__
            )

            with track_state() as run2:
                assert runner() == 0

            assert run1.ppid != run2.ppid
            assert run1.pid != run2.pid

            assert run2.pid != test_pid
            assert run2.ppid != test_pid


def test_server_bypass_ok():
    # Given the server_bypass decorator parameter is True
    # And the server is up
    # When the decorated function is executed
    # Then the server should not receive the command
    # And the command should be processed
    def bypass_server():
        return True

    @cli_factory(bypass_server=bypass_server)
    def runner():
        def inner():
            record()
            return 0

        return inner

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid == test_pid


def test_unwritable_runtime_dir_raises_exception():
    # Given a runtime_dir path pointing to a location that is not writable
    # And the server is not up
    # When the decorated function is executed
    # Then the server should fail to come up
    # And it should raise a QuickenError with message 'runtime directory \'{}\'
    #  is not writable'
    with isolated_filesystem() as path:
        @cli_factory(runtime_dir_path=path)
        def runner():
            def inner():
                pass

            return inner

        Path(path).chmod(stat.S_IRUSR | stat.S_IWUSR)

        with contained_children():
            with pytest.raises(QuickenError) as e:
                runner()

        assert f'{path} must have permissions 700' in str(e)


def test_server_not_creating_socket_file_raises_exception(mocker):
    # Given the server is not up
    # And the server has been stubbed out to not create the socket file
    # When the decorated function is executed
    # Then it should time out waiting for the socket file to be created and
    #  raise a QuickenError with message 'timed out connecting to server'
    mocker.patch('quicken.lib._server.run')
    @cli_factory()
    def runner():
        def inner():
            pass

        return inner

    with contained_children():
        with pytest.raises(QuickenError):
            runner()


def test_server_not_listening_on_socket_file_raises_exception(mocker):
    # Given the server is not up
    # And the server has been stubbed out to bind to the socket file but not
    #  listen
    # When the decorated function is executed
    # Then it should raise a QuickenError with message 'failed to connect to
    #  server'
    run_function = mocker.patch('quicken.lib._server.run')

    def fake_listener(*_args, **_kwargs):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socket_name)

    run_function.side_effect = fake_listener

    with isolated_filesystem() as path:
        @cli_factory(runtime_dir_path=path)
        def runner():
            def inner():
                pass

            return inner

        with contained_children():
            with pytest.raises(QuickenError) as e:
                runner()


@pytest.mark.skip
@pytest.mark.timeout(5)
def test_runner_fails_when_communicating_to_stopped_server():
    # Given a server that is running but has received SIGSTOP
    # When the decorated function is executed.
    # Then the client should not hang
    # And should raise a QuickenError with message 'failed to communicate with server'
    with isolated_filesystem() as path:

        @cli_factory(runtime_dir_path=path)
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
    @cli_factory()
    def runner():
        def inner():
            record()

        return inner

    test_pid = os.getpid()

    with contained_children():
        with track_state() as run1:
            assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid
        assert not active_children(), \
            f'Active children present: {[c.pid for c in active_children()]}'

        with track_state() as run2:
            assert runner() == 0

        assert run1.pid != run2.pid
        assert run1.ppid == run2.ppid


def test_runner_rejects_on_different_uids():
    # Given a user is executing the decorated function
    # And their real, effective, or saved uid or gid are different
    # Then the function should raise a QuickenError with the reason
    # Unless the server is bypassed
    def bypass_handler():
        return bypass

    bypass = False

    @cli_factory(bypass_server=bypass_handler)
    def runner():
        def inner():
            return 0

        return inner

    cases = [
        [(3, 3, 2), ['real', 'effective']],
        [(3, 2, 3), ['real', 'saved']],
        [(2, 3, 3), ['effective', 'saved']],
        [(2, 3, 4), ['real', 'effective', 'saved']],
    ]

    with contained_children():
        for case in cases:
            id_result, expected_strings = case

            def patched():
                return id_result

            with patch('os.getresuid', patched):
                with pytest.raises(QuickenError) as e:
                    runner()

                for s in expected_strings:
                    assert s in str(e)

                bypass = True
                assert runner() == 0
                bypass = False

            with patch('os.getresgid', patched):
                with pytest.raises(QuickenError) as e:
                    runner()

                for s in expected_strings:
                    assert s in str(e)

                bypass = True
                assert runner() == 0
                bypass = False


def test_runner_reloads_server_on_different_groups():
    # Given the server has been started with supplemental groups 1, 2, 3
    # And the decorated function is executed with supplemental groups 1, 2
    # Then the server should be reloaded, and the decorated function executed
    @cli_factory()
    def runner():
        def inner():
            record()

        return inner

    test_pid = os.getpid()

    with contained_children():
        with patch('os.getgroups', lambda: (1, 2, 3)):
            with track_state() as run1:
                assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        with patch('os.getgroups', lambda: (1, 2)):
            with track_state() as run2:
                assert runner() == 0

        assert run2.pid != test_pid
        assert run2.ppid != test_pid
        assert run1.ppid != run2.ppid
        assert run1.pid != run2.pid


def test_runner_reloads_server_on_different_gid():
    # Given the server has been started with real gid 1
    # And the decorated function is executed with real gid 2
    # Then the server should be reloaded, and the decorated function executed
    @cli_factory()
    def runner():
        def inner():
            record()

        return inner

    test_pid = os.getpid()

    with contained_children():
        with patch('os.getgid', lambda: 1):
            with track_state() as run1:
                assert runner() == 0

        assert run1.pid != test_pid
        assert run1.ppid != test_pid

        with patch('os.getgid', lambda: 2):
            with track_state() as run2:
                assert runner() == 0

        assert run2.pid != test_pid
        assert run2.ppid != test_pid
        assert run1.ppid != run2.ppid
        assert run1.pid != run2.pid

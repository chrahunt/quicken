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
    # Given a socket_file that exists
    # And the daemon is not up (indicated by a missing pid)
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
    #  an exception.
    ...


def test_daemon_not_creating_socket_file_raises_exception():
    # Given the daemon is not up
    # And the daemon has been stubbed out to not create the socket file
    # When the decorated function is executed
    # Then it should time out waiting for the socket file to be created and
    #  raise an exception.
    ...

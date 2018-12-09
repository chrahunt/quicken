import os
import logging
import logging.config
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid

from daemon.pidfile import TimeoutPIDLockFile
import pytest

from example.daemon.server import run, client


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            '()': UTCFormatter,
            'format': '#### [{asctime}][{levelname}][{name}]\n    {message}',
            'style': '{',
        }
    },
    'handlers': {
        'file': {
            '()': 'logging.FileHandler',
            'level': 'DEBUG',
            'filename': 'pytest.log',
            'encoding': 'utf-8',
            'formatter': 'default',
        }
    },
    'root': {
        'handlers': ['file'],
        'level': 'DEBUG',
    }
})


logging.basicConfig(level=logging.DEBUG, filename='logs.log')
logger = logging.getLogger(__name__)


def noop(*_args, **_kwargs):
    pass


def daemon_runner(fn, *args, **kwargs):
    """Run daemonizing function in a compatible way.

    Requires pytest be invoked with `-s`, otherwise python-daemon fails to dup
    stdin/out/err.
    """
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
    else:
        # When debugging in PyCharm, closerange takes forever so we replace it
        # here.
        os.closerange = noop
        try:
            # Stay in the current directory.
            fn(*args, **kwargs, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, working_directory=os.getcwd())
        except:
            logger.exception('Exception running daemon function.')
            raise


def wait_for_file(path, timeout, interval=0.01):
    while timeout > 0:
        if Path(path).exists():
            break
        time.sleep(interval)
        timeout -= interval


@pytest.mark.skip
def test_daemon_starts():
    f = TimeoutPIDLockFile('daemon.pid')
    daemon_runner(run, noop, 'socket', pidfile=f)
    wait_for_file(f.path, 2)
    assert Path(f.path).exists()
    pid = Path(f.path).read_text(encoding='utf-8')
    os.kill(int(pid), signal.SIGTERM)


def test_daemon_communicates():
    logger.debug('test_daemon_communicates()')
    with tempfile.NamedTemporaryFile() as f:
        def write_file(handler_sock):
            data = handler_sock.recv(1024).decode('utf-8')
            Path(f.name).write_text(data, encoding='utf-8')
        pidf = TimeoutPIDLockFile('daemon.pid')
        socket_file = 'socket'
        daemon_runner(run, write_file, socket_file, pidfile=pidf)
        wait_for_file(pidf.path, 2)
        assert Path(pidf.path).exists()
        pid = Path(pidf.path).read_text(encoding='utf-8')
        logger.info('Daemon pid: %s', pid)
        data = str(uuid.uuid4())
        with client(socket_file) as sock:
            sock.sendall(data.encode('utf-8'))
        wait_for_file(f.name, 2)
        time.sleep(0.5)
        contents = f.read().decode('utf-8')
        os.kill(int(pid), signal.SIGTERM)
    assert contents == data

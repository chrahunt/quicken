import logging
import socket
import sys

from example.daemon.fd import max_pid_len, send_fds
from example.daemon.protocol import serialize_state

logger = logging.getLogger(__name__)


def run():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect('socket_file')
            print('connected')
        except ConnectionRefusedError:
            logger.warning('Connection refused')
            return False
        state = serialize_state()
        send_fds(
            sock, f'{len(state)}'.encode('ascii'),
            [sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()])
        print('sent fds')
        data = sock.recv(max_pid_len)
        # TODO: Setup signals.
        logger.debug('Request being handled by pid %s', data)
        sock.sendall(state)
        # rc
        data = sock.recv(3)
        logger.debug('Child finished %s', data)
    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    run()

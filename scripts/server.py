"""Generic process spawning facility for repeatable well-behaved processes.

Requirements:

- spawned processes must have the same attributes as the client:
  - cwd
  - environment
  - args
- spawned processes must react in an obvious way to signals sent by the
  human user to the client, e.g.
  - stop
  - interrupt
  - kill
"""
import atexit
import daemon
import logging
import logging.handlers
import os
from pathlib import Path
import socketserver
import sys

from example.daemon.fd import max_pid_len, recv_fds
from example.daemon.protocol import deserialize_state


logger = logging.getLogger(__name__)




def get_request_handler(callback):
    class ForkingUnixRequestHandler(socketserver.BaseRequestHandler):
        """Sets process state to match the client and then invokes callback.

        Protocol:

        all strings are encoded utf-8

        client sends message with sendmesg with contents:
        - msg: length of next message as string representation of integer
        - 3 fds: stdin, stdout, stderr

        server -> client:
        - pid of handling process

        client -> server:
        all fields separated by null
        - argc (as string)
        - *argv
        - cwd
        - k=v for k, v in os.environ

        server sets these values and starts execution of the callback

        client waits for any data or disconnect.
        """
        def handle(self):
            """Caller is expected to send message, defined as:

                pgid \0 mesg
            """
            logger.debug('handle()')
            # TODO: Ensure that we are in the same session as the caller
            # TODO: os.setpgid to match the caller.
            # TODO: return our pid.
            # stdout, stdin, stderr
            max_fds = 3
            msglen, fds = recv_fds(self.request, max_pid_len, max_fds)
            msglen = int(msglen)

            logger.info('Received message %s, num fds: %s', msglen, len(fds))

            if len(fds) != 3:
                logger.error('Received unexpected number of fds.')
                return
            stdin = os.fdopen(fds[0])
            stdout = os.fdopen(fds[1], 'w')
            stderr = os.fdopen(fds[2], 'w')
            reset_loggers(stdout, stderr)
            sys.stdin = stdin
            sys.stdout = stdout
            sys.stderr = stderr

            logger.info('After basicConfig()')
            self.request.sendall(f'{os.getpid()}'.encode('ascii'))
            contents = b''
            while len(contents) != msglen:
                contents += self.request.recv(4096)
            state = deserialize_state(contents)
            os.chdir(state['cwd'])
            os.environ = state['env']
            sys.argv = state['argv']
            # Execute callback.
            # TODO: In sub-thread.
            rc = callback()
            if not rc:
                rc = 1
            # Send return code.
            self.request.sendall(f'{rc}'.encode('ascii'))

    return ForkingUnixRequestHandler


class ForkingUnixServer(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    pass


def run(callback, socket_file):
    logger.debug('run()')
    try:
        Path(socket_file).unlink()
    except:
        if Path(socket_file).exists():
            raise

    def cleanup():
        try:
            Path(socket_file).unlink()
        except:
            if Path(socket_file).exists():
                raise
    atexit.register(cleanup)

    server = ForkingUnixServer(socket_file, get_request_handler(callback))
    logger.info('starting()')
    server.serve_forever()


def main():
    def cb():
        logger.info('cb()')

    filename = './socket_file'

    print('main')
    with daemon.DaemonContext(working_directory=os.getcwd()):
        logging.basicConfig(level=logging.DEBUG, filename='server.log')
        try:
            run(cb, filename)
        except:
            logger.exception('Received exception running daemon.')


if __name__ == '__main__':
    print('__main__')
    main()

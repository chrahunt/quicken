import array
from pathlib import Path
import socket


def send_fds(sock, msg, fds):
    return sock.sendmsg([msg], [
        (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', fds))
    ])


def recv_fds(sock, msglen, maxfds):
    fds = array.array('i')
    msg, ancdata, flags, addr = sock.recvmsg(msglen, socket.CMSG_LEN(maxfds * fds.itemsize))
    for cmsg_level, cmsg_type, cmsg_data in ancdata:
        if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
            fds.fromstring(cmsg_data[:len(cmsg_data) - (len(cmsg_data) % fds.itemsize)])
    return msg, list(fds)


max_pid_len = len(Path('/proc/sys/kernel/pid_max').read_bytes())

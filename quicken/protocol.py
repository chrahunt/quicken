import os
import sys
from typing import Dict


def deserialize_state(data: bytes) -> Dict:
    result = {}
    args = data.decode('utf-8').split('\x00')
    argc = int(args[0])
    result['argv'] = args[1:argc+1]
    result['cwd'] = args[argc+1]
    env = {}
    for kv in args[argc+2:]:
        k, v = kv.split('=', 1)
        env[k] = v
    result['env'] = env
    return result


def serialize_state() -> bytes:
    # all fields separated by null
    # - argc (as string)
    # - *argv
    # - cwd
    # - k=v for k, v in os.environ
    args = []
    args.append(str(len(sys.argv) - 1))
    args.extend(sys.argv[1:])
    args.append(os.getcwd())
    args.extend(f'{k}={v}' for k, v in os.environ.items())
    return '\x00'.join(args).encode('utf-8')

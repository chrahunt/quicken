"""CLI for running Python code in an application server.

Argument types:
[command, '--', *args]
['-f', file, '--', *args]
['-h']
['-c', code, '--', *args]
['-m', module, '--', *args]
['-s', script, '--', *args]

The general issue this solves is: given a single piece of executable code, split
it into two parts:

1. The part useful to preload that can be executed without issues
2. The part that should not be executed unless explicitly requested

MainProvider: Splitting the code and executing the code matched to part 1
Main: part 2
"""
from __future__ import annotations

#import demandimport
#demandimport.ignore('_bootlocale')
#demandimport.ignore('_compat_pickle')
#demandimport.ignore('asyncio.base_tasks')
#demandimport.enable()
from ._timings import report
report('start cli load')

import argparse
import ast
import hashlib
import importlib
import os
import stat
import sys

from functools import partial


from .lib._typing import MYPY_CHECK_RUNNING
from .lib._xdg import RuntimeDir

if MYPY_CHECK_RUNNING:
    from typing import List


report('end cli load dependencies')

def run(name, metadata, callback):
    report('start quicken load')
    from .lib import quicken
    report('end quicken load')
    def reload(old_data, new_data):
        return old_data != new_data
    return quicken(name, reload_server=reload, user_data=metadata)(callback)()


def is_main(node):
    if not isinstance(node, ast.If):
        return False

    test = node.test

    if not isinstance(test, ast.Compare):
        return False

    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False

    if len(test.comparators) != 1:
        return False

    left = test.left
    right = test.comparators[0]

    if isinstance(left, ast.Name):
        name = left
    elif isinstance(right, ast.Name):
        name = right
    else:
        return False

    if isinstance(left, ast.Str):
        main = left
    elif isinstance(right, ast.Str):
        main = right
    else:
        return False

    if name.id != '__name__':
        return False

    if not isinstance(name.ctx, ast.Load):
        return False

    if main.s != '__main__':
        return False

    return True


# XXX: May be nicer to use a Loader implemented for our purpose.
def parse_file(path: str):
    """
    Given a path, parse it into a
    Parse a file into prelude and main sections.

    We assume that the "prelude" is anything before the first "if __name__ == '__main__'".

    Returns annotated code objects as expected.
    """
    with open(path, 'rb') as f:
        text = f.read()

    root = ast.parse(text, filename=path)
    for i, node in enumerate(root.body):
        if is_main(node):
            split = i
            break
    else:
        raise RuntimeError('Must have if __name__ == "__main__":')

    prelude = ast.copy_location(
        ast.Module(root.body[:i]), root
    )
    main = ast.copy_location(
        ast.Module(root.body[i:]), root
    )
    prelude_code = compile(prelude, filename=path, dont_inherit=True, mode="exec")
    main_code = compile(main, filename=path, dont_inherit=True, mode="exec")
    # Shared context.
    context = {
        '__name__': '__main__',
        '__file__': path,
    }
    prelude_func = partial(exec, prelude_code, context)
    main_func = partial(exec, main_code, context)
    return prelude_func, main_func


def handle_code(text: str) -> int:
    """
    Given some python text, get the imports and outputs, then treat the function
    as a module.
    Args:
        text:

    Returns:
    """
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()

    def importer():
        root = ast.parse(text)
        imports = extract_imports(root)
        for name in imports:
            importlib.import_module(name)

        def inner():
            pass

        return inner

    name = f'quicken.code.{digest}'
    return run(name, {}, importer)


class PathHandler:
    def __init__(self, path):
        report('start handle_path()')
        if not os.path.exists(path):
            print(f'{path} does not exist')
        path = os.path.abspath(path)
        real_path = os.path.realpath(path)
        digest = hashlib.sha256(path.encode('utf-8')).hexdigest()
        stat_result = os.stat(real_path)
        self._path = path
        self._name = f'quicken.file.{digest}'
        self._metadata = {
            'path': path,
            'real_path': real_path,
            'ctime': stat_result[stat.ST_CTIME],
            'mtime': stat_result[stat.ST_MTIME],
        }

    @property
    def name(self):
        return self._name

    @property
    def metadata(self):
        return self._metadata

    def main(self):
        report('start file processing')
        prelude_code, main_code = parse_file(self._path)
        # Execute everything before if __name__ == '__main__':
        prelude_code()
        report('end file processing')
        # Pass main back to be executed by the server.
        return main_code


def handle_module(text: str) -> int:
    """
    For modules it can go several ways:
    1. top-level module which does have if __name__ == "__main__" (pytest)
    2. __main__ module which does have if __name__ == "__main__" (pip)
    3. __main__ module which does not have if __name__ == "__main__" (flit)
    4. __main__ module  which does have if __name__ == "__main__" but does imports
       underneath it (poetry) - ouch

    These are usually pretty small anyways, so it's probably OK to handle them
    a little more loosely than scripts.
    """
    pass


def handle_command(text: str) -> int:
    ...


def preprocess_args(args: List[str]) -> List[str]:
    if not args[0].startswith('-'):
        # Leading command, just insert -- and return.
        return [args[0], '--', *args[1:]]

    # Otherwise, insert after terminal arguments.
    terminal_args = '-f', '-m'
    out = []
    args = iter(args)
    for arg in args:
        if arg in terminal_args:
            out.append(arg)
            try:
                out.append(next(args))
            except StopIteration:
                # TODO: Error, command requires argument.
                return []
            out.append('--')
            out.extend(args)
    return out


def main():
    report('start main()')
    # TODO: Python version guards.
    parser = argparse.ArgumentParser(
        description='''
        Invoke Python commands in an application server.
        '''
    )
    parser.add_argument(
        '--ctl',
        choices=['status', 'stop'],
        help='server control'
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument('-f', help='path to script')
    selector.add_argument('-m', help='module')
    parser.add_argument('args', nargs='*')
    args = preprocess_args(sys.argv)
    if not args:
        parser.print_usage(sys.stderr)
    args = parser.parse_args()
    if args.f:
        path = args.f
        if not os.path.exists(path):
            print(f'{path} does not exist')
        handler = PathHandler(path)
    else:
        parser.print_usage(sys.stderr)
        sys.exit(1)

    if args.ctl:
        from .lib._lib import CliServerManager, ConnectionFailed

        # TODO: De-duplicate runtime dir name construction.
        runtime_dir = RuntimeDir(f'quicken-{handler.name}')

        manager = CliServerManager(runtime_dir)

        with manager.lock:
            try:
                client = manager.connect()
            except ConnectionFailed:
                print('Server down')
                sys.exit(0)
            else:
                client.close()

            if args.ctl == 'status':
                print(manager.server_state)
            elif args.ctl == 'stop':
                manager.stop_server()
            else:
                sys.stderr.write('Unknown action')
                sys.exit(1)
    else:
        sys.exit(run(handler.name, handler.metadata, handler.main))

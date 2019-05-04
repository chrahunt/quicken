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

For the invoked code we try to align with the equivalent Python behavior, where
it makes sense:

1. For files:
   1. Python sets __file__ to the path as provided to the Python interpreter,
      but since initial import occurs in a directory that may be different than
      the runtime execution directory, we normalize __file__ to be an absolute
      path - any usages of __file__ within methods would then be correct.
   2. Python sets sys.argv[0] to the path as provided to the Python interpreter,
      this behavior is OK since if relative it will be relative to cwd which will
      be set by the time the if __name__ == '__main__' block is called.
1. For modules:
   1. Python sets __file__ to the absolute path of the file, we should do the same.
   2. Python sets sys.argv[0] to the absolute path of the file, we should do the same.
"""
from __future__ import annotations

from ._timings import report
report('start cli load')

import argparse
import ast
import importlib.util
import os
import stat
import sys

try:
    # Faster to import than hashlib if _sha512 is present. See e.g. python/cpython#12742
    from _sha512 import sha512 as _sha512
except ImportError:
    from hashlib import sha512 as _sha512

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

    log_file = os.environ.get('QUICKEN_LOG')

    decorator = quicken(
        name, reload_server=reload, log_file=log_file, user_data=metadata
    )

    return decorator(callback)()


def is_main(node):
    """Whether a node represents:
    if __name__ == '__main__':
    if '__main__' == __name__:
    """
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
    path = os.path.abspath(path)
    with open(path, 'rb') as f:
        text = f.read()

    root = ast.parse(text, filename=path)
    for i, node in enumerate(root.body):
        if is_main(node):
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


class PathHandler:
    def __init__(self, path, args):
        report('start handle_path()')
        self._path_arg = path

        path = os.path.abspath(path)
        self._path = path

        self._args = args

        real_path = os.path.realpath(path)
        digest = _sha512(path.encode('utf-8')).hexdigest()
        self._name = f'quicken.file.{digest}'

        stat_result = os.stat(real_path)

        self._metadata = {
            'path': path,
            'real_path': real_path,
            'ctime': stat_result[stat.ST_CTIME],
            'mtime': stat_result[stat.ST_MTIME],
        }

    @property
    def argv(self):
        return [self._path_arg, *self._args]

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


# Adapted from https://github.com/python/cpython/blob/e42b705188271da108de42b55d9344642170aa2b/Lib/runpy.py#L101
# with changes:
# * we do not actually want to retrieve the module code yet
def _get_module_details(mod_name, error=ImportError):
    if mod_name.startswith("."):
        raise error("Relative module names not supported")
    pkg_name, _, _ = mod_name.rpartition(".")
    if pkg_name:
        # Try importing the parent to avoid catching initialization errors
        try:
            __import__(pkg_name)
        except ImportError as e:
            # If the parent or higher ancestor package is missing, let the
            # error be raised by find_spec() below and then be caught. But do
            # not allow other errors to be caught.
            if e.name is None or (e.name != pkg_name and
                    not pkg_name.startswith(e.name + ".")):
                raise
        # Warn if the module has already been imported under its normal name
        existing = sys.modules.get(mod_name)
        if existing is not None and not hasattr(existing, "__path__"):
            from warnings import warn
            msg = "{mod_name!r} found in sys.modules after import of " \
                "package {pkg_name!r}, but prior to execution of " \
                "{mod_name!r}; this may result in unpredictable " \
                "behaviour".format(mod_name=mod_name, pkg_name=pkg_name)
            warn(RuntimeWarning(msg))

    try:
        spec = importlib.util.find_spec(mod_name)
    except (ImportError, AttributeError, TypeError, ValueError) as ex:
        # This hack fixes an impedance mismatch between pkgutil and
        # importlib, where the latter raises other errors for cases where
        # pkgutil previously raised ImportError
        msg = "Error while finding module specification for {!r} ({}: {})"
        raise error(msg.format(mod_name, type(ex).__name__, ex)) from ex
    if spec is None:
        raise error("No module named %s" % mod_name)
    if spec.submodule_search_locations is not None:
        if mod_name == "__main__" or mod_name.endswith(".__main__"):
            raise error("Cannot use package as __main__ module")
        try:
            pkg_main_name = mod_name + ".__main__"
            return _get_module_details(pkg_main_name, error)
        except error as e:
            if mod_name not in sys.modules:
                raise  # No module loaded; being a package is irrelevant
            raise error(("%s; %r is a package and cannot " +
                               "be directly executed") %(e, mod_name))
    loader = spec.loader
    if loader is None:
        raise error("%r is a namespace package and cannot be executed"
                                                                 % mod_name)
    return mod_name, spec


class ModuleHandler:
    """
    For modules it can go several ways:
    1. top-level module which does have if __name__ == "__main__" (pytest)
    2. __main__ module which does have if __name__ == "__main__" (pip)
    3. __main__ module which does not have if __name__ == "__main__" (flit)
    4. __main__ module which does have if __name__ == "__main__" but does imports
       underneath it (poetry)

    As a result, and since they are pretty small usually, we can be more flexible
    with parsing -
    1. extract all top-level import statements or import statements under if __name__ == "__main__"
       into their own unit
    2. execute the import unit at server start
    3. execute the rest
    this will only cause problems if some tool has order-dependent imports underneath e.g. a
    platform check and we can trace a warning if that is the case.
    """
    def __init__(self, module_name, args):
        report('start ModuleHandler')
        self._module_name, self._spec = _get_module_details(module_name)


    def main(self):
        loader = self._spec.loader
        try:
            code = loader.get_code(self._module_name)
        except ImportError as e:
            raise ImportError(format(e)) from e
        if code is None:
            raise ImportError("No code object available for %s" % self._module_name)


def parse_args(args=None):
    #args = preprocess_args(args)
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
    # We have to have an option name otherwise the first value in `args` might
    # be taken as the script path.
    selector.add_argument('-f', help='path to script')
    # Deferred.
    #selector.add_argument('-m', help='module name')

    parser.add_argument('args', nargs='*')

    parsed = parser.parse_args(args)
    return parser, parsed


def main():
    report('start main()')
    parser, args = parse_args()
    if args.f:
        path = args.f
        if not os.path.exists(path):
            parser.error(f'{path} does not exist')
        handler = PathHandler(path, args.args)
    #elif args.m:
    #    # We do not have a good strategy for avoiding import of the parent module
    #    # so for now just reject.
    #    if '.' in args.m:
    #        parser.error('Sub-modules are not supported')
    #    handler = ModuleHandler(args.m, args.args)
    else:
        parser.print_usage(sys.stderr)
        sys.exit(1)

    sys.argv = handler.argv

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

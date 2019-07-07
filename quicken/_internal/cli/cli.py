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

import argparse
import ast
import importlib.util
import logging
import os
import stat
import sys

from functools import partial

from ._logging import default_configuration
from .helpers import CliServerManager, Commands
from .parsing import is_main
from .._imports import sha512
from .._typing import MYPY_CHECK_RUNNING
from ..constants import DEFAULT_IDLE_TIMEOUT, ENV_IDLE_TIMEOUT, ENV_LOG_FILE
from ..timings import report
from ..xdg import RuntimeDir

if MYPY_CHECK_RUNNING:
    from typing import Callable, List, Optional, Tuple


logger = logging.getLogger(__name__)


def run(name, metadata, callback, reload_callback=None):
    report("start quicken load")
    from ..decorator import quicken

    report("end quicken load")

    if reload_callback is None:

        def reload_callback(old_data, new_data):
            return old_data != new_data

    idle_timeout = float(os.environ.get(ENV_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT))

    decorator = quicken(
        name,
        reload_server=reload_callback,
        server_idle_timeout=idle_timeout,
        user_data=metadata,
    )

    return decorator(callback)()


# XXX: May be nicer to use a Loader
def parse_file(path: str) -> Tuple[Callable, Callable]:
    """
    Call "if __name__ == '__main__'" a "main_check".

    Parse a file into pre-main_check (prelude) and post-main_check (main) callables.

    The returned functions share context, so executing prelude then main should
    let main see all the things defined by prelude.
    """
    path = os.path.abspath(path)
    with open(path, "rb") as f:
        text = f.read()

    root = ast.parse(text, filename=path)
    for i, node in enumerate(root.body):
        if is_main(node):
            break
    else:
        raise RuntimeError('Must have if __name__ == "__main__":')

    prelude = ast.copy_location(ast.Module(root.body[:i]), root)
    main_part = ast.copy_location(ast.Module(root.body[i:]), root)
    prelude_code = compile(prelude, filename=path, dont_inherit=True, mode="exec")
    main_code = compile(main_part, filename=path, dont_inherit=True, mode="exec")
    # Shared context.
    context = {"__name__": "__main__", "__file__": path}
    prelude_func = partial(exec, prelude_code, context)
    main_func = partial(exec, main_code, context)
    return prelude_func, main_func


class PathHandler:
    def __init__(self, path):
        """
        Args:
            path: path to the file to process
            args: arguments to be used for the sub-process
        Raises:
        """
        report("start handle_path()")
        self._path_arg = path

        path = os.path.abspath(path)
        self._path = path

        real_path = os.path.realpath(path)
        digest = sha512(real_path.encode("utf-8")).hexdigest()
        self._name = f"quicken.file.{digest}"

        stat_result = os.stat(real_path)

        logger.debug("Digest: %s", digest)

        self._metadata = {
            "path": path,
            "real_path": real_path,
            "ctime": stat_result[stat.ST_CTIME],
            "mtime": stat_result[stat.ST_MTIME],
        }

        logger.debug("Metadata: %s", self._metadata)

    @property
    def argv_prefix(self):
        return [self._path_arg]

    def main(self):
        report("start file parsing")
        prelude_code, main_code = parse_file(self._path)
        report("end file parsing")
        # Execute everything before if __name__ == '__main__':
        report("start prelude execute")
        prelude_code()
        report("end prelude execute")
        # Pass main back to be executed by the server.
        return main_code

    @property
    def metadata(self):
        return self._metadata

    @property
    def name(self):
        return self._name

    def reload_callback(self, old_data, new_data):
        return (
            old_data["ctime"] != new_data["ctime"]
            or old_data["mtime"] != new_data["mtime"]
        )


# Adapted from https://github.com/python/cpython/blob/e42b705188271da108de42b55d9344642170aa2b/Lib/runpy.py#L101
# with changes:
# * we do not actually want to retrieve the module code yet (defeats the purpose
#   of our script)
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
            if e.name is None or (
                e.name != pkg_name and not pkg_name.startswith(e.name + ".")
            ):
                raise

        # Warn if the module has already been imported under its normal name
        existing = sys.modules.get(mod_name)
        if existing is not None and not hasattr(existing, "__path__"):
            from warnings import warn

            msg = (
                "{mod_name!r} found in sys.modules after import of "
                "package {pkg_name!r}, but prior to execution of "
                "{mod_name!r}; this may result in unpredictable "
                "behaviour".format(mod_name=mod_name, pkg_name=pkg_name)
            )
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
            raise error(
                ("%s; %r is a package and cannot " + "be directly executed")
                % (e, mod_name)
            )
    loader = spec.loader
    if loader is None:
        raise error("%r is a namespace package and cannot be executed" % mod_name)
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
        report("start ModuleHandler")
        self._module_name, self._spec = _get_module_details(module_name)

    def main(self):
        loader = self._spec.loader
        try:
            code = loader.get_code(self._module_name)
        except ImportError as e:
            raise ImportError(format(e)) from e
        if code is None:
            raise ImportError("No code object available for %s" % self._module_name)


class ConfigurationError(Exception):
    """Error in CLI that should be propagated to the user.
    """

    pass


def handle_logging_options(args=None):
    log_file = None
    if args:
        log_file = args.log_file
    if not log_file:
        log_file = os.environ.get(ENV_LOG_FILE)

    if not log_file:
        return

    try:
        default_configuration(log_file)
    except PermissionError:
        raise ConfigurationError(f'Log file "{log_file}" is not writable.')


def add_logging_options(parser):
    parser.add_argument(
        "--log-file",
        help=f"path to file in which to write logs, required if -v is provided. May also be provided with {ENV_LOG_FILE}.",
    )


RUN_COMMAND = "run"


def get_arg_parser():
    def add_command_type_group(parser):
        selector = parser.add_mutually_exclusive_group(required=True)
        selector.add_argument("--file", help="path to script")
        # selector.add_argument('--entrypoint', help='spec as would be provided to console_scripts')
        # selector.add_argument('--from-path', help='name of command to search for in PATH')
        return selector

    parser = argparse.ArgumentParser(
        description="Run Python commands in an application server."
    )

    subparsers = parser.add_subparsers(
        description="", dest="action", metavar="<subcommand>", required=True
    )

    run_parser = subparsers.add_parser(
        RUN_COMMAND, description="Run a command on a quicken server.", help="run code"
    )
    add_logging_options(run_parser)
    add_command_type_group(run_parser)
    run_parser.add_argument(
        "args",
        help="arguments to pass to the underlying command",
        nargs=argparse.REMAINDER,
    )

    status_parser = subparsers.add_parser(
        Commands.STATUS, description="Get server status.", help="get server status"
    )
    add_logging_options(status_parser)
    add_command_type_group(status_parser)
    status_parser.add_argument(
        "--json", action="store_true", help="output status data as JSON"
    )

    stop_parser = subparsers.add_parser(
        Commands.STOP, description="Stop server.", help="stop server if it is running"
    )
    add_logging_options(stop_parser)
    add_command_type_group(stop_parser)

    return parser


def _main(args) -> Optional[int]:
    handle_logging_options(args)

    if args.file:
        path = args.file

        if not os.access(path, os.R_OK):
            raise ConfigurationError(f"Cannot read {path}.")

        try:
            handler = PathHandler(path)
        except FileNotFoundError:
            raise ConfigurationError(f"{path} does not exist.")

    # elif args.m:
    #    # We do not have a good strategy for avoiding import of the parent module
    #    # so for now just reject.
    #    if '.' in args.m:
    #        parser.error('Sub-modules are not supported')
    #    handler = ModuleHandler(args.m, args.args)

    if args.action == RUN_COMMAND:
        cmd_args = args.args
        # argparse.REMAINDER leaves a leading --.
        if cmd_args and cmd_args[0] == "--":
            cmd_args.pop(0)
        # Reset sys.argv for quicken propagation.
        # noinspection PyUnboundLocalVariable
        sys.argv = handler.argv_prefix + cmd_args

        return run(
            handler.name, handler.metadata, handler.main, handler.reload_callback
        )

    from ..lib import ServerManager

    name = handler.name

    runtime_dir = RuntimeDir(name)

    manager = ServerManager(runtime_dir)

    with manager.lock:
        cli_manager = CliServerManager(manager, sys.stdout)

        if args.action == Commands.STATUS:
            cli_manager.print_status(json_format=args.json)

        elif args.action == Commands.STOP:
            cli_manager.stop()


def main():
    report("start main()")

    parser = get_arg_parser()
    args = parser.parse_args()

    try:
        sys.exit(_main(args))
    except ConfigurationError as e:
        parser.error(e.args[0])

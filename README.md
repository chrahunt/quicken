# quicken

[![PyPI Version](https://img.shields.io/pypi/v/quicken.svg)](https://pypi.org/project/quicken/)
[![Documentation](https://readthedocs.org/projects/quicken/badge/)](https://quicken.readthedocs.io/en/latest/)
[![Build Status](https://dev.azure.com/chrahunt/quicken/_apis/build/status/chrahunt.quicken?branchName=master)](https://dev.azure.com/chrahunt/quicken/_build/latest?definitionId=1&branchName=master)
[![Python Versions](https://img.shields.io/pypi/pyversions/quicken.svg)](https://pypi.org/project/quicken/)

Make Python tools start fast.

When a quickened script is executed the first time it starts a server in the
background, paying a one time cost to speed up execution for every other execution.

Quicken only speeds up applications on Linux, but transparently falls back
to executing scripts directly on unsupported platforms, with minimal overhead.

Generally, an application can benefit if:

1. It takes more than 100ms to start on an average machine
1. `python -X importtime` shows that the startup time is related to module
   importing

To see how fast an app can be, check out the latest benchmark results in
[CI](https://dev.azure.com/chrahunt/quicken/_build/latest?definitionId=1&branchName=master) and
interpretation in wiki [here](https://github.com/chrahunt/quicken/wiki/Benchmark-interpretation).

## Usage

### `quicken.script`

`quicken.script` is a helper that can wrap `console_scripts` as supported by several Python packaging tools.

If our console script is `hello=hello.cli:main`, then to use `quicken.script` we would add
`helloc=quicken.script:hello.cli._.main`.

In words: replace ":" with `._.` and prepend `quicken.script:`.

Once set up, we can use `helloc` just like `hello`, but it should be faster after the first time.

Since `quicken` is still alpha software, it would be wise to provide a second
command for testing as above, instead of only having a quicken-based command. We
use a `c` suffix since it's a `c`lient.

If using setuptools (`setup.py`):

```python
setup(
    # ...
    entry_points={
        'console_scripts': [
            'hello=hello.cli:main',
            # With quicken
            'helloc=quicken.script:hello.cli._.main',
        ],
    },
    # ...
)
```

If using poetry

<!--
double-quotes needed for TOML syntax highlighter, otherwise making docs yields
error: WARNING: Could not lex literal_block as "toml". Highlighting skipped.
-->
```toml
[tools.poetry.scripts]
hello = "hello.cli:main"
# With quicken
helloc = "quicken.script:hello.cli._.main"
```

If using flit

```toml
[tools.flit.scripts]
hello = "hello.cli:main"
# With quicken
helloc = "quicken.script:hello.cli._.main"
```

### `quicken.ctl_script`

Similar to the above, using `quicken.ctl_script` provides a CLI to stop and
check the status of a quicken server.

Setuptools example:

```python
setup(
    ...
    entry_points={
        'console_scripts': [
            'hello=hello.cli:main',
            # With quicken
            'helloc=quicken.script:hello.cli._.main',
            # Server control command
            'helloctl=quicken.ctl_script:hello.cli._.main',
        ],
    },
    ...
)
```

Then we can use `helloctl status` to see the server status information and
`helloctl stop` to stop the application server.

### `quicken` CLI

The `quicken` command can be use to quicken plain Python scripts that look like

```python
# script.py
...

def main():
    pass


if __name__ == '__main__':
    main()
```

Running `quicken -f script.py` followed by arguments will start the application server and
run all code before `if __name__ == '__main__'`. For the first and subsequent commands, only
the code in `if __name__ == '__main__'` will be executed. If the script is updated then a new
server will be started.

To see the status of the server: `quicken --ctl status -f script.py`

To stop the server: `quicken --ctl stop -f script.py`

The server is identified using the full path to the script.

#### Differences

1. `__file__` is set to the full, resolved path to the file provided to `-f`, unlike
   Python which sets it to the path provided on the command line. This is so the
   code before `if __name__ == '__main__'` and the code after it see the same path
   even if the user changes directories or the path provided to the command.

# Why

Python command-line tools can feel slow. There are tricks that can be used to
speed up startup, but implementing them in individual packages is not scalable,
and can slow development. The purpose of this project is:

1. provide one way to speed up app startup, with a focus on strategies that
   can apply across a large number of applications using normal Python
   development conventions
1. find areas of improvement that can be folded back into Python itself
1. make it easier to focus on application logic and not startup time concerns

# Limitations

* Unix only.
* Debugging may be less obvious for end users or contributors.
* Access to the socket file implies access to the server and ability to run commands. The library tries to
  mandate that the directory used for runtime files is only owned by the user, for best results use
  `XDG_RUNTIME_DIR` as provided by `pam_systemd` or the equivalent for your distribution.

# Tips

* Profile import time with -X importtime, see if your startup is actually the
  problem. If it's not then this package will not help you.
* Ensure your package can be built as a wheel, even if it's not distributed as
  one. When wheels are installed they create scripts that do not import `pkg_resources`,
  which can save 60ms+ depending on disk speed and caching.

# Development

```shell
poetry install
poetry run pytest -ra
```

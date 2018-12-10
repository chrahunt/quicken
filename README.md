# quicken

Make Python tools fast.

```python
# app/cli.py
import slow_module
import has_lots_of_dependencies


def cli():
    print('hello world')
    # Finally get to work after everything is loaded.
    slow_module.do_work(has_lots_of_dependencies)
    

# app/main.py
from quicken import cli_factory


@cli_factory('app')
def main():
    from .cli import cli
    return cli
```

That's it! The first time `main()` is invoked a server will be created and
stay up even after the process finishes. When another process starts up it
will request the server to execute `cli` instead of reloading all modules
(and dependencies) from disk.

If `python -c ''` takes 10ms, this module takes around 40ms. That's how
fast your command-line apps can start every time (after the first 😉).

# Why

Python command-line tools are slow. We can reduce dependencies, do lazy
importing, and do little/no work at the module level but these can only go
so far.

Our goal is to speed up the cli without giving up any dependencies. Every Python
CLI tool should be able to get to work in less than 100ms.

# Goals

* Be as fast as possible when invoked as a client, be pretty fast when invoked
  and we need to start a server.

# Limitations

* Unix only.
* Debugging may be less obvious for end users or contributors.
* Daemon will not automatically have updated gid list if user was modified.
* Access to the socket file implies access to the daemon (and the associated command that it would run if asked).

# Tips

* Profile import time with -X importtime, see if your startup is actually the
  problem. If it's not then this package will not help you.
* Distribute your package as a wheel. When wheels are installed they create
  scripts that do not import `pkg_resources`, which can save 60ms+ depending
  on disk speed and caching.

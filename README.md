# quicken

Make Python tools fast.

Before:

```python
import sys

import slow_module
import has_lots_of_dependencies


def main():
    print('hello world')
    slow_module.do_work(has_lots_of_dependencies)


if __name__ == '__main__':
    sys.exit(main())
```

After:

```python
import sys

from quicken import quicken


@quicken('app')
def main_wrapper():
    import slow_module
    import has_lots_of_dependencies

    def main():
        print('hello world')
        slow_module.do_work(has_lots_of_dependencies)

    return main


if __name__ == '__main__'
    sys.exit(main_wrapper())
```

That's it! The first time `main()` is invoked a server will be created and
stay up even after the process finishes. When another process starts up it
will request the server to execute `main`.
 
This speeds up command-line applications with startup bottlenecks related to
module loading and initialization.

If `python -c ''` takes 10ms, this module takes around 40ms. That's how
fast your command-line apps can start every time after the server is up.


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
* Access to the socket file implies access to the daemon (and the associated command that it would run if asked).

# Tips

* Profile import time with -X importtime, see if your startup is actually the
  problem. If it's not then this package will not help you.
* Distribute your package as a wheel. When wheels are installed they create
  scripts that do not import `pkg_resources`, which can save 60ms+ depending
  on disk speed and caching.

# Development

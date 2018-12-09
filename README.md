# fast cli

Make Python CLI tools snappy.

# Why

Python command-line tools are slow. We can reduce dependencies, do lazy
importing, and do little/no work at the module level but these can only go
so far before impacting the development experience.

Our goal is to speed up the cli without giving up any dependencies. Every Python
CLI tool should be able to get to work in less than 100ms.

# Goals

* Be as fast as possible when invoked as a client, be pretty fast when invoked
  and we need to start a server.

# Limitations

* Unix only.
* Debugging may be less obvious for end users or contributors.
* Daemon will not automatically have updated gid list if user was modified.
* Access to the socket file implies access to the command.

# Tips

* Profile import time with -X importtime, see if your startup is actually the
  problem. If it's not then this package will not help you.
* Distribute your package as a wheel. When wheels are installed they create
  scripts that do not import `pkg_resources`, which can save 60ms+ depending
  on hardware.

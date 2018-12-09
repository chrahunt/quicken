# fast cli

Make Python CLI tools snappy.

# Why

Python command-line tools are slow. We can reduce dependencies, do lazy
importing, and do little/no work at the module level but these can only go
so far before impacting the development experience.

Our goal is to speed up the cli without giving up any dependencies. Every Python
CLI tool should be able to get to work in less than 100ms.

# Limitations

* Unix only.
* Debugging may be less obvious for end users.
* If there's an error on running the cli factory function then it shows up twice.

# Goals

* Be as fast as possible when invoked as a client, be pretty fast when invoked
  and we need to start a server.

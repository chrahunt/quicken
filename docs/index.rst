.. mdinclude:: ../README.md

Differences and restrictions
============================

The library tries to be transparent for applications. Specifically here is the behavior you can expect:

* command-line arguments: ``sys.argv`` is set to the list of arguments of the client
* file descriptors for stdin/stdout/stderr: ``sys.stdin``, ``sys.stdout``, and ``sys.stderr`` are sent from the client to the command process, any console loggers
  are re-initialized with the new streams
* environment: ``os.environ`` is copied from the client to the command process
* current working directory: we change directory to the cwd of the client process
* umask is set to the same as the client

The setup above is guaranteed to be done before the registered script function
is invoked.

In addition:

* signals sent to the client that can be forwarded are sent to the command process
* for SIGTSTP (C-z at a terminal) and SIGTTIN, we send SIGSTOP to the command process and then stop the client process
* for SIGKILL, which cannot be intercepted, the server recognizes when the connection to the client is broken and will
  kill the command process soon after
* when the command runner exits, the client exits with the same exit code
* if a client and the server differ in group id or supplementary group ids then a new
  server process is started before the command is run

While the registered script function is executed in a command process, the initial import of
the module is done by the first client that is executed. For that reason, there are several things that
should be avoided outside of the registered script function:

1. starting threads - because we fork to create the command runner process, it may cause undesirable effects if
   threads are created
2. read configuration based on environment, arguments, or current working directory - if done when a module is imported
   then it will capture the values of the client that started the server
3. set signal handlers - this will only be setting signal handlers for the first client starting
   the server and these are overridden to forward signals to the command runner process
4. start sub-processes
5. reading plugin information (from e.g. ``pkg_resources``) - this will only be at server start time,
   and not when the command is actually run

Currently the following is unsupported at any point:

* ``atexit`` handlers - they will not be run at the end of the handler process
* ``setuid`` or ``setgid`` are currently unsupported


.. toctree::
   :maxdepth: 2
   :caption: Contents:

   api


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

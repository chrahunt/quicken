quicken
=======

Quicken is a library that helps Python applications start more quickly, with a focus on doing the "right thing" for
wrapping CLI-based tools.

When a quicken-wrapped command is executed the first time, an application server will be started. If the server
is already up then the command will be executed in a ``fork``ed child, which avoids the overhead of loading
libraries.

Quicken only speeds up applications on Unix platforms, but falls back to executing commands directly
on non-Unix platforms.

The library tries to be transparent for applications. Every time a command is run all context is sent from the
client to the server, including:

* command-line arguments
* current working directory
* environment
* umask
* file descriptors for stdin/stdout/stderr

``quicken.script``
==================

For command-line tool authors that want to speed up applications, simply add quicken as a
dependency, then use ``quicken.script`` in your ``console_scripts`` (or equivalent).

If your existing entry point is ``my_app.cli:main``, then you would use ``quicken.script:my_app.cli._.main``.

For example, if using setuptools (``setup.py``):

.. code-block:: python

   setup(
       ...
       entry_points={
           'console_scripts': [
               'my-command=my_app.cli:main',
               # With quicken
               'my-commandc=quicken.script:my_app.cli._.main',
           ],
       },
       ...
   )

If using poetry

.. code-block:: toml

   [tools.poetry.scripts]
   poetry = 'poetry:console.run'
   # With quicken
   poetryc = 'quicken.script:poetry._.console.run'

If using flit

.. code-block:: toml

   [tools.flit.scripts]
   flit = "flit:main"
   # With quicken
   flitc = "quicken.script:flit._.main"


``quicken`` command
===================

The ``quicken`` command can be used with basic scripts and command-line tools that do not use quicken built-in.

Given a script ``script.py``, like

.. code-block:: python

   import click
   import requests

   ...

   @click.command()
   def main():
       """My script."""
       ...

   if __name__ == '__main__':
       main()


running ``quicken -f script.py arg1 arg2`` once will start the application server then run ``main()``. Running the command
again like ``quicken -f script.py arg2 arg3`` will run the command on the server, and should be faster

If ``script.py`` is changed then the server will be restarted the next time the command is run.


Differences and restrictions
============================

The library tries to be transparent for applications, but it cannot be exactly the same. Specifically here
is the behavior you can expect:

* ``sys.argv`` is set to the list of arguments of the client
* ``sys.stdin``, ``sys.stdout``, and ``sys.stderr`` are sent from the client to the command process, any console loggers
  are re-initialized with the new streams
* ``os.environ`` is copied from the client to the command process
* we change directory to the cwd of the client process

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

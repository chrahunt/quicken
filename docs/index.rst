quicken
===================================

Quicken is a Python package that enables CLI-based tools to start more quickly.

When added to a command-line tool, Quicken starts a server transparently the first time the tool is invoked.
After the server is running, it is responsible for executing commands. This is fast because imports happen once at
server start.

The library is transparent for users. Every time a command is run all context is sent to the server, including:

* arguments
* current working directory
* environment
* umask
* file descriptors for stdin/stdout/stderr

Usage:

Assume your application is ``my_app`` and your original CLI entrypoint is ``my_app.cli.cli``. Create a file ``my_app/cli_wrapper.py``, with contents:

.. code-block:: python

   from quicken import cli_factory


   @cli_factory('my_app')
   def main():
       # Import your existing command-line entrypoint.
       # This is the expensive operation that only happens once.
       from .cli import cli
       # Return it.
       return cli

Adapt ``setup.py``:

.. code-block:: python

   setup(
       ...
       entry_points={
           'console_scripts': ['my-command=my_app.cli_wrapper:cli']
       },
       ...
   )

If you have ``my_app/__main__.py``, it should look like:

.. code-block:: python

   from .cli_wrapper import main


   main()

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   api


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

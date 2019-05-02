def test_file_evaluation():
    # Given a package hello with
    #
    # hello/
    #   __init__.py
    #   foo.py
    #
    # # hello/__init__.py
    # foo = 1
    #
    # # script.py
    # from hello import foo
    # import hello.foo
    #
    # if __name__ == '__main__':
    #     print(foo)
    #
    # should print 1
    ...


def test_file_backtrace_line_numbering():
    # Given a file `script.py`:
    #
    # import os
    # raise RuntimeError('example')
    #
    # if __name__ == '__main__':
    #     raise RuntimeError('example2')
    #
    # When executed, the backtrace should have RuntimeError('example') coming
    # from the appropriate location.
    ...


def test_file_main_backtrace_line_numbering():
    # Given a file `script.py`:
    #
    # import os
    #
    # if __name__ == '__main__':
    #     os.getpid
    #     raise RuntimeError('example')
    #
    # When executed, the backtrace should have RuntimeError('example') coming
    # from the appropriate location.
    ...


def test_file_path_set():
    # Given a file `script.py`
    # Executed like python script.py
    # The Python interpreter sets __file__ to the value passed as the first
    # argument.
    # The problem with that is we execute main from a cwd that is different than
    # the initial execution for the module.
    # So we normalize the __file__ attribute to always be the full, resolved path
    # to the file.
    ...


def test_file_path_set_symlink():
    # Given a file `script.py`
    # __file__ should be the full, resolved path to the file.
    ...

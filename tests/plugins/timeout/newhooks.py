from pytest import hookspec


@hookspec
def pytest_timeout_timeout(item, report):
    """Function called when timeout occurs.

    item is the item that timed out.

    report is the fake report that will be traced.
    """

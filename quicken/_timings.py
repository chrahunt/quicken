import os
import time


def report(name):
    """Used for reporting important timings.

    PYTHONHUNTER='kind="call", function="report", module="quicken._timings"'
    """
    if os.environ.get('QUICKEN_TRACE_TIMINGS'):
        print(f'{time.perf_counter()}: {name}')

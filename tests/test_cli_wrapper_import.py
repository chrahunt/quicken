import timeit


def time_test(**kwargs):
    t = timeit.Timer()

def test_cli_wrapper_import_time_is_not_high():
    # Should be higher than python -c ''
    # But not higher than python -c 'import quicken.
    ...

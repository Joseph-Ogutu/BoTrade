import pytest

from derivbot.collector.backfill import (
    BackfillError,
    decimals_from_pip,
    validate_page,
)


def test_pip_size_forms_seen_on_the_wire():
    assert decimals_from_pip(2) == 2
    assert decimals_from_pip(2.0) == 2
    assert decimals_from_pip(0.01) == 2
    assert decimals_from_pip(0.001) == 3
    assert decimals_from_pip(1e-05) == 5
    with pytest.raises(ValueError):
        decimals_from_pip(0)


def test_valid_page_passes():
    validate_page([5, 6, 7], end=7, prev_min=None, limit=1000)
    validate_page([5, 6, 7], end="latest", prev_min=None, limit=1000)
    validate_page([1, 3, 5], end=9, prev_min=None, limit=1000)  # ticks may stop before `end`
    validate_page([1, 2, 3], end=5, prev_min=4, limit=1000)


@pytest.mark.parametrize("times,end,prev,why", [
    ([], 5, None, "empty"),
    ([5, 5, 6], 6, None, "not strictly increasing"),
    ([7, 6], 7, None, "not strictly increasing"),
    ([5, 6, 8], 7, None, "later than requested end"),       # the silent fallback signature
    ([3, 4, 5], 9, 4, "overlaps the previous page"),
])
def test_invalid_pages_are_rejected(times, end, prev, why):
    with pytest.raises(BackfillError) as ei:
        validate_page(times, end=end, prev_min=prev, limit=1000)
    assert why.split()[0] in str(ei.value)


def test_page_over_the_limit_is_rejected():
    with pytest.raises(BackfillError):
        validate_page(list(range(1001)), end="latest", prev_min=None, limit=1000)

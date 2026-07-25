'''assemble_ranges: grouping units into contiguous ranges with a max-gap bridge.'''
from oe2d import contests


def test_empty():
    assert contests.assemble_ranges([]) == []


def test_single():
    assert contests.assemble_ranges([5]) == [(5, 5)]


def test_contiguous_run():
    assert contests.assemble_ranges([1, 2, 3]) == [(1, 3)]


def test_bridges_gap_within_max():
    # default max_gap=2: a two-unit hole (2 -> 5) is bridged into one range.
    assert contests.assemble_ranges([1, 2, 5]) == [(1, 5)]


def test_splits_when_gap_exceeds_max():
    # 2 -> 6 is a three-unit hole, past max_gap=2, so it splits.
    assert contests.assemble_ranges([1, 2, 6]) == [(1, 2), (6, 6)]


def test_unsorted_and_duplicate_units():
    assert contests.assemble_ranges([3, 1, 2, 2]) == [(1, 3)]


def test_max_gap_zero_only_bridges_adjacent():
    assert contests.assemble_ranges([1, 2], max_gap=0) == [(1, 2)]
    assert contests.assemble_ranges([1, 3], max_gap=0) == [(1, 1), (3, 3)]

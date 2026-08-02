'''Tests for _normalize_table_columns (hermetic: plain grids in, plain grids out).

_normalize_table_columns runs on every flat table read_flat_tables produces, one hop before the
grids reach scope_flat_tables. Its job is to make the SAME contest come back the same width on every
page: a scanned SOVC prints each candidate as a count column followed by a percent column under one
(colspan-2) header, and Textract segments that pair inconsistently -- sometimes count and percent
fused in ONE cell ("428 76.16%"), sometimes SPLIT into a standalone percent column ("22.24%") -- so
page A can come back narrower than page B for the same contest. This strips the columns that carry
nothing we keep (a standalone percent column, an all-empty spacer) while leaving a fused
count+percent cell in place (its count is recovered downstream from the leading token). If a percent
column slips through, the contest's width diverges page to page and scope_flat_tables drops the odd
page -- so this is worth pinning tightly, with grids shaped like the real Columbia US Senate scan.
'''
import pytest

from ... import votes


def _percent_cells(grid: list[list[str]]) -> list[str]:
    '''Every cell that is a bare percent -- what normalization is supposed to leave none of.'''
    return [cell for row in grid for cell in row if votes._PERCENT_CELL.match(votes._clean(cell))]


def test_drops_a_standalone_percent_column():
    # Columbia page-3 shape: Casey's count and percent split into two cells (a standalone percent
    # column), the other candidate fused ("428 76.16%"); the percent column is stripped, the fused
    # cell stays (its count is read from the leading token downstream)
    grid = [
        ['', 'Total Votes', 'CASEY', '', 'MCCORMICK', 'Write-in'],
        ['BEAVER TWP', '562', '125', '22.24%', '428 76.16%', '1 0.18%'],
        ['BENTON TWP', '773', '189', '24.45%', '569 73.61%', '0'],
        ['BENTON BORO', '386', '120', '31.09%', '243 62.95%', '0'],
    ]
    out = votes._normalize_table_columns(grid)
    assert [len(row) for row in out] == [5, 5, 5, 5]
    assert out[1] == ['BEAVER TWP', '562', '125', '428 76.16%', '1 0.18%']
    assert not _percent_cells(out)


def test_keeps_a_fused_count_percent_column():
    # a column whose cells fuse count and percent ("10 20.0%") is NOT a pure-percent column, so it is
    # kept intact -- the count is recovered from the leading token later, not here
    grid = [['', 'Harris'], ['P1', '10 20.0%'], ['P2', '30 60.0%'], ['P3', '5 10.0%']]
    out = votes._normalize_table_columns(grid)
    assert [row[1] for row in out[1:]] == ['10 20.0%', '30 60.0%', '5 10.0%']


def test_drops_an_all_empty_spacer_column():
    grid = [['', 'Harris', '', 'Trump'], ['P1', '10', '', '20'], ['P2', '30', '', '40']]
    out = votes._normalize_table_columns(grid)
    assert [len(row) for row in out] == [3, 3, 3]
    assert out[1] == ['P1', '10', '20']


def test_count_only_grid_passes_through_unchanged():
    grid = [['', 'Harris', 'Trump'], ['P1', '10', '20'], ['P2', '30', '40']]
    assert votes._normalize_table_columns(grid) == grid


def test_empty_grid_is_returned_as_is():
    assert votes._normalize_table_columns([]) == []


@pytest.mark.xfail(strict=True, reason='short-page percent columns slip through: the >= 3-row floor '
                   'in drop() leaves a standalone percent column on a page with only two data rows '
                   '(Columbia US Senate page 4), so that page stays wider than its sibling and '
                   'scope_flat_tables drops it. Remove this marker when the floor is repaired.')
def test_drops_a_standalone_percent_column_on_a_short_page():
    # Columbia page-4 shape: the same split-percent layout, but only two data rows (one precinct plus
    # the county Total). The standalone percent column must still be stripped so the page matches its
    # taller sibling's width -- this is the divergence that drops the continuation page today.
    grid = [
        ['', 'Total Votes', 'CASEY', '', 'MCCORMICK', 'Write-in'],
        ['SUGARLOAF', '555', '119', '21.44%', '425 76.58%', '0'],
        ['Total', '32404', '10962', '33.83%', '20600 63.57%', '29 0.09%'],
    ]
    out = votes._normalize_table_columns(grid)
    assert [len(row) for row in out] == [5, 5, 5]
    assert not _percent_cells(out)

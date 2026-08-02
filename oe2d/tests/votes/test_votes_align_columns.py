'''Tests for _align_columns (hermetic): mapping a table's columns to the anchor's candidates by
CONTENT, so a continuation page is read by matching candidate names in its own header rather than by
assuming an identical column layout. This is the deterministic alignment that replaces the brittle
exact-column-count gate -- it must tolerate a split column, an inserted spacer, and a shifted layout,
fall back to anchor positions for a header-less continuation, and reject a foreign table.
'''
from ... import votes

# Two candidates, at columns 1 and 2 in the anchor.
NAMES = ['Kamala D. Harris', 'Donald J. Trump']
ANCHOR = [1, 2]


def test_matches_candidates_to_their_header_columns():
    grid = [['', 'HARRIS', 'TRUMP'], ['P1', '10', '20'], ['P2', '30', '40']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {0: 1, 1: 2}


def test_tolerates_a_shifted_layout():
    # a spacer column pushes TRUMP from col 2 (its anchor position) to col 3; content still finds it
    grid = [['', 'HARRIS', '', 'TRUMP'], ['P1', '10', '', '20']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {0: 1, 1: 3}


def test_ignores_a_split_off_percent_column():
    # HARRIS's percent split into its own column (col 2, no counts); TRUMP's count is at col 3.
    # a percent-only column is never a candidate's value column, so it is skipped
    grid = [['', 'HARRIS', '', 'TRUMP'], ['P1', '10', '76.16%', '20'], ['Total', '30', '60.00%', '40']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {0: 1, 1: 3}


def test_matches_a_name_spread_over_multiple_header_rows():
    # a title row above the candidate-name row (Columbia's "PRESIDENTIAL ELECTORS" shape): the header
    # is every row above the first data row, so the name is found even under a title
    grid = [['', 'PRESIDENTIAL', 'ELECTORS'], ['', 'HARRIS', 'TRUMP'], ['P1', '10', '20']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {0: 1, 1: 2}


def test_header_less_continuation_reuses_anchor_positions():
    # no header (first row is already data): a pure continuation of the anchor's layout, so keep the
    # anchor's column positions rather than trying to match absent header text
    grid = [['P1', '10', '20'], ['P2', '30', '40']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {0: 1, 1: 2}


def test_foreign_turnout_table_matches_nothing():
    # a turnout block ("Times Cast", "Registered Voters") of the same width names no candidate, so it
    # maps to nothing and the caller drops it
    grid = [['', 'Times Cast', 'Registered Voters'], ['P1', '322', '438'], ['P2', '390', '569']]
    assert votes._align_columns(grid, NAMES, ANCHOR) == {}


def test_empty_grid_maps_nothing():
    assert votes._align_columns([], NAMES, ANCHOR) == {}


# --- geometry path: names decide identity, column x-centres decide position ---

# The anchor's two candidates sit at x-centres 0.30 and 0.42.
ANCHOR_X = [0.30, 0.42]


def test_geometry_maps_each_candidate_to_the_nearest_count_column():
    grid = [['', 'HARRIS', 'TRUMP'], ['P1', '10', '20']]
    column_x = [0.05, 0.30, 0.42]
    got = votes._align_columns(grid, NAMES, ANCHOR, column_x=column_x, anchor_x=ANCHOR_X)
    assert got == {0: 1, 1: 2}


def test_geometry_survives_a_percent_column_split_from_its_count():
    # TRUMP's percent splits into its own column (col 2, x 0.36, no counts); TRUMP's count keeps its
    # x (col 3, 0.42). By x the count column wins, where a column-index match would have shifted
    grid = [['', 'HARRIS', 'TRUMP', ''], ['P1', '10', '20', '76.0%'], ['Total', '30', '60', '66.0%']]
    column_x = [0.05, 0.30, 0.42, 0.47]
    got = votes._align_columns(grid, NAMES, ANCHOR, column_x=column_x, anchor_x=ANCHOR_X)
    assert got == {0: 1, 1: 2}                          # not col 3 (that is the percent)


def test_geometry_still_rejects_a_neighbouring_contest_by_name():
    # even with geometry, identity is by NAME: a table naming a different contest's candidates is not
    # this contest, so it maps to nothing regardless of where its columns sit
    grid = [['', 'SMITH', 'JONES'], ['P1', '10', '20']]
    column_x = [0.05, 0.30, 0.42]
    assert votes._align_columns(grid, NAMES, ANCHOR, column_x=column_x, anchor_x=ANCHOR_X) == {}


def test_geometry_header_less_continuation_maps_by_x():
    # a header-less continuation (first row is data) with geometry maps each candidate to the count
    # column nearest its anchor x
    grid = [['P1', '10', '20'], ['P2', '30', '40']]
    column_x = [0.05, 0.30, 0.42]
    got = votes._align_columns(grid, NAMES, ANCHOR, anchor_width=3, column_x=column_x, anchor_x=ANCHOR_X)
    assert got == {0: 1, 1: 2}

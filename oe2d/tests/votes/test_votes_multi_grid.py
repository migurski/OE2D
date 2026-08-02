'''Tests for segment_multi_grid (hermetic: a hand-built mega-grid in, per-contest sub-tables out).

A MEGA-GRID is one physical table whose several contests SHARE the precinct rows and partition the
COLUMNS -- one row per precinct spans every contest side-by-side (Missaukee MI). Aligning a contest's
candidates among ALL the columns is ambiguous (a surname like "Stein" belongs to two contests), so the
grid is first cut into one flat sub-table per contest. The cut is deterministic from the printed
structure: the party-header row repeats its first label ("Dem") at each partisan block's start, and the
block before it (party-name choices, no party header) is Straight Party. This pins that segmentation.
'''
from ... import votes

# columns: 0 Registered, 1 Poll Book, 2 Precinct(label), 3-4 Straight Party, 5-6 President, 7-8 Senate
GRID = [
    ['', '', '', '', '', 'President', '', 'Senate', ''],          # r0 titles
    ['', '', '', '', '', 'Dem', 'Rep', 'Dem', 'Rep'],             # r1 party header: Dem restarts col 5, 7
    ['Reg', 'Poll', 'Precinct', 'Dem', 'Rep', 'Harris', 'Trump', 'Slotkin', 'Rogers'],   # r2 names
    ['100', '90', 'Alpha', '5', '10', '40', '50', '42', '48'],    # r3 data (a precinct)
    ['', '', 'Total', '10', '20', '80', '100', '84', '96'],       # r4 county total
]
COLUMN_X = [0.02, 0.06, 0.10, 0.20, 0.24, 0.40, 0.44, 0.60, 0.64]


def test_cuts_one_subtable_per_contest():
    blocks = votes.segment_multi_grid(GRID, COLUMN_X)
    assert len(blocks) == 3                                       # Straight Party, President, Senate


def test_each_block_is_the_label_column_plus_its_own_candidate_columns():
    straight, president, senate = votes.segment_multi_grid(GRID, COLUMN_X)
    assert straight['columns'] == [2, 3, 4] and straight['names'] == ['Dem', 'Rep']
    assert president['columns'] == [2, 5, 6] and president['names'] == ['Harris', 'Trump']
    assert senate['columns'] == [2, 7, 8] and senate['names'] == ['Slotkin', 'Rogers']


def test_subtable_keeps_all_rows_including_the_total_row():
    president = votes.segment_multi_grid(GRID, COLUMN_X)[1]
    assert president['grid'][2] == ['Precinct', 'Harris', 'Trump']      # name row, sliced to the block
    assert president['grid'][3] == ['Alpha', '40', '50']               # a precinct's counts
    assert president['grid'][-1] == ['Total', '80', '100']             # the reconciliation row survives
    assert president['column_x'] == [0.10, 0.40, 0.44]                  # x-centres sliced to match


def test_straight_party_block_precedes_the_first_partisan_block():
    # the Dem/Rep NAME columns with an empty party-header above them are Straight Party, kept as their
    # own block ahead of President -- its "candidates" are party abbreviations, matched later by party
    straight = votes.segment_multi_grid(GRID, COLUMN_X)[0]
    assert straight['names'] == ['Dem', 'Rep']

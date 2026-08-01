'''Tests for the schema-driven walker and stitch (hermetic: no LM, no source files).

These exercise the deterministic half of oe2d.votes -- given a PageSchema (what the interpreter
would return), the walker must pull the right cells and the stitch must reassemble precincts. The
walker holds no English of its own, so a stub schema fully drives it.
'''
from ... import votes
from ...votes import signatures


def _schema(**kwargs):
    base = dict(first_data_row=0, label_column=0, columns=[], method_labels={}, skip_labels=[])
    base.update(kwargs)
    return signatures.PageSchema(**base)


def test_walk_page_wrapped_label_and_methods():
    # a precinct whose name wraps across two rows, then Election Day / Total sub-rows
    rows = [
        ['Title banner', 'Title banner'],                 # skipped by first_data_row
        ['Big Creek Township, Precinct', ''],
        ['1', ''],
        ['Election Day', '172'],
        ['Total', '222'],
    ]
    schema = _schema(first_data_row=1, columns=[signatures.ColumnRole(index=1, role='candidate')],
                     method_labels={'Election Day': 'election_day', 'Total': 'total'})
    blocks = votes.walk_page(rows, schema)
    assert len(blocks) == 1
    assert blocks[0]['label'] == 'Big Creek Township, Precinct 1'
    assert set(blocks[0]['methods']) == {'election_day', 'total'}


def test_walk_page_stops_at_terminal_skip_label():
    rows = [
        ['Precinct A', ''], ['Total', '10'],
        ['County Total', '99'],                            # skip label after real blocks -> stop
        ['Total', '99'],                                   # trailing junk, must not become a block
    ]
    schema = _schema(columns=[signatures.ColumnRole(index=1, role='candidate')],
                     method_labels={'Total': 'total'}, skip_labels=['County Total'])
    blocks = votes.walk_page(rows, schema)
    assert [b['label'] for b in blocks] == ['Precinct A']


def test_precinct_groups_split_on_repeated_candidate():
    a = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Harris')])
    b = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Oliver')])
    c = _schema(columns=[signatures.ColumnRole(index=1, role='candidate', candidate='Harris')])
    pages = [(a, []), (b, []), (c, [])]                    # Harris repeats on page 3 -> new group
    groups = votes._precinct_groups(pages)
    assert [len(g) for g in groups] == [2, 1]


def test_votes_to_rows_canonical_shape():
    votes_map = {('Precinct A', 'Kamala D. Harris', 'DEM'): {'votes': 222, 'election_day': 172}}
    rows = votes.votes_to_rows(votes_map, county='Oscoda', office='President')
    assert list(rows[0].keys()) == list(votes.CANON_COLUMNS)
    assert rows[0]['candidate'] == 'Kamala D. Harris' and rows[0]['votes'] == 222
    assert rows[0]['early_voting'] == ''


def test_norm_is_whitespace_and_case_insensitive():
    assert votes._norm('DEM HARRIS and  WALZ') == votes._norm('dem harris andwalz')


class _Role:
    def __init__(self, row_index):
        self.row_index = row_index


def test_consolidate_write_in_prefers_a_flagged_total_else_sums_components():
    # the interpreter marks which columns are an aggregate total vs components; no numeric guessing
    assert votes._consolidate_write_in([], [0, 0, 0, 2]) == 2   # components only -> sum
    assert votes._consolidate_write_in([], [6, 2]) == 8         # Barry: scattered + qualified add up
    assert votes._consolidate_write_in([2], [2]) == 2          # Adams: total over its sole breakdown
    assert votes._consolidate_write_in([3], [1, 1, 1]) == 3    # an explicit grand total wins
    assert votes._consolidate_write_in([], []) == 0


def test_reconciles_confirms_a_ruled_scan_read_against_printed_totals():
    # precinct rows that sum to the printed county totals per candidate reconcile
    votes_map = {('P1', 'Harris', 'DEM'): {'votes': 10}, ('P2', 'Harris', 'DEM'): {'votes': 20},
                 ('P1', 'Trump', 'REP'): {'votes': 5}, ('P2', 'Trump', 'REP'): {'votes': 7}}
    assert votes._reconciles(votes_map, {('Harris', 'DEM'): 30, ('Trump', 'REP'): 12}) is True
    # method-sub-row content mis-read as flat doubles every column -> fails (the Gogebic case)
    assert votes._reconciles(votes_map, {('Harris', 'DEM'): 15, ('Trump', 'REP'): 6}) is False
    # no printed totals captured -> cannot confirm -> False (caller prefers the cheap read)
    assert votes._reconciles(votes_map, {}) is False
    # a single OCR digit slip in one of several columns is outvoted by the majority that match
    many = {('P1', c, 'X'): {'votes': 10} for c in 'ABCDE'}
    totals = {(c, 'X'): 10 for c in 'ABCD'}
    totals[('E', 'X')] = 999                       # one column off
    assert votes._reconciles(many, totals) is True
    # write-in rows are excluded from the per-candidate sums
    with_wi = dict(votes_map)
    with_wi[('P1', votes.WRITE_IN_LABEL, '')] = {'votes': 3}
    assert votes._reconciles(with_wi, {('Harris', 'DEM'): 30, ('Trump', 'REP'): 12}) is True


def test_split_party_pulls_a_trailing_party_out_of_the_name():
    assert votes._split_party('Kamala D. Harris (DEM)', '') == ('Kamala D. Harris', 'DEM')
    assert votes._split_party('Kamala D. Harris', 'DEM') == ('Kamala D. Harris', 'DEM')  # already split
    assert votes._split_party('Write-ins', '') == ('Write-ins', '')


def test_count_columns_outvotes_a_stray_cell():
    # every candidate row has counts at cols 2,5,7,9; one row has a stray count at col 3 -> excluded
    grid = [
        ['BIDEN', 'DEM', '5', '', '9.62%', '163', '38.63%', '0', '0.00%', '168', '35.44%'],
        ['TRUMP', 'REP', '43', '8', '2.69%', '248', '58.77%', '0', '0.00%', '291', '61.39%'],
        ['HAWKINS', 'GRN', '1', '', '1.0%', '2', '1.0%', '0', '0.00%', '3', '1.0%'],
    ]
    rows = [_Role(0), _Role(1), _Role(2)]
    assert votes._count_columns(grid, rows, 4) == [2, 5, 7, 9]


def test_contiguous_label_stops_at_gap():
    # precinct name may wrap into the adjacent cell; a far-column banner past a gap is dropped
    assert votes._contiguous_label(['Gettysburg', '1', '', '', 'banner'], 0) == 'Gettysburg 1'
    assert votes._contiguous_label(['110', '', '817 of', '1,056'], 0) == '110'


def test_assign_methods_equal_count_zips_in_order():
    rec = votes._assign_methods(['total', 'election_day', 'absentee_mail', 'provisional'],
                                [150, 100, 48, 2])
    assert rec == {'votes': 150, 'election_day': 100, 'absentee_mail': 48, 'provisional': 2}


def test_assign_methods_recovers_dropped_component_via_total_checksum():
    # a zero provisional cell was dropped -> 3 cells, 4 buckets; total (275=35+240) realigns them
    rec = votes._assign_methods(['election_day', 'absentee_mail', 'provisional', 'total'],
                                [35, 240, 275])
    assert rec == {'election_day': 35, 'absentee_mail': 240, 'provisional': 0, 'votes': 275}


def test_cell_count_reads_merged_count_and_skips_percent():
    assert votes._cell_count('1 100.00%') == 1        # count merged with its percent
    assert votes._cell_count('86.32%') is None        # pure percent -> not a count
    assert votes._cell_count('1,234') == 1234         # comma stripped


def test_assign_methods_ignores_a_spurious_wedged_cell_via_prefix_sum():
    # provisional 3 present, total 235 == 53+179+3; a stray 5 wedged before the total is dropped
    rec = votes._assign_methods(['election_day', 'absentee_mail', 'provisional', 'total'],
                                [53, 179, 3, 5, 235])
    assert rec == {'election_day': 53, 'absentee_mail': 179, 'provisional': 3, 'votes': 235}
